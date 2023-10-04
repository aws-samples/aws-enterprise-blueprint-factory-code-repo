import yaml
import boto3
import subprocess
from botocore.exceptions import ClientError
import logging
import json
import os
import subprocess
import time

log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(format = log_format, level = logging.INFO)
logger = logging.getLogger()

logger.info("Starting...")
logger.info(os.environ)

exitcode = 0

SSM_PRODUCT_PREFIX = "/blueprints/service-catalog/{}/AdminProduct".format(os.getenv('ENVIRONMENTTYPE'))
SSM_PORTFOLIO_PREFIX="/blueprints/service-catalog/{}/Portfolio".format(os.getenv('ENVIRONMENTTYPE'))


def Convert_Key_Value(key_value_dict):
    key_value_list = []
    for k,v in key_value_dict.items():
        key_value_list.append({'Key': k,'Value': v})
    return key_value_list

session = boto3.Session()

mySts = session.client('sts').get_caller_identity()
CODEBUILD_ROLE = mySts["Arn"]

ssm_client = session.client('ssm')
servicecatalog_client = session.client('servicecatalog')
s3_client = boto3.client('s3',region_name=os.getenv("AWS_REGION"))
cfb_client = boto3.client('cloudformation')

try:
    response = s3_client.upload_file('{}/{}/empty_cfn.yaml'.format(os.getenv("CODEBUILD_SRC_DIR"),"Product"), os.getenv("ARTIFACT_BUCKET"), "empty_cfn.yaml",ExtraArgs={"ServerSideEncryption": "aws:kms"})
except ClientError as e:
    logging.error(e)

conf_product_dev_admin_list = [
    {
        'name' : 'Bootstrapping-Admin-Product',
        'owner' : 'ServiceCatalogAdminTeam',
        'description' : 'This product is for Blueprint administration only and used to release Blueprints to ServiceCatalog',
        'stack_tags' : {
            'DataClassification' : 'Confidential',
            'Organization' : 'AWS',
            'ProductType' : 'Admin'
            },
        'product_config_file' : "bp-pipeline/product_config.json",
        'portfolio_associations' : ['Bootstrapping-Admin-Portfolio'],
    }
]


logger.info("iterating through")
# Iterate through different products mentioned in the config_admin file
for conf_product in conf_product_dev_admin_list:
    product_name = conf_product['name']
    product_owner = conf_product['owner']
    product_description = conf_product['description']
    Product_Tags = []
    for tag in conf_product['stack_tags']:
        Product_Tags.append({'Key': tag, 'Value': conf_product["stack_tags"][tag]})
    ProductConfigFile = "{}/Product/{}".format(os.getenv("CODEBUILD_SRC_DIR"), conf_product['product_config_file'])
    with open(ProductConfigFile, "r") as product_config_file:
        product_config_file_json = yaml.safe_load(product_config_file)        
    product_version = product_config_file_json['ProductVersionName']
    product_version_description = product_config_file_json['ProductVersionDescription']
    product_template = product_config_file_json['Properties']['TemplateFilePath']

    # Checks for any existing product with the same name. If not, it'll create a new one, else, update the existing one with new package
            
    response_search_product = servicecatalog_client.search_products_as_admin(Filters={'FullTextSearch' : [ conf_product['name']]})
    if not response_search_product['ProductViewDetails']:
        # cloudformation package
        subprocess.run(
        [
            "aws", "cloudformation", "package",
            "--region", os.getenv("AWS_REGION"),
            "--template-file", "{}/Product/{}".format(os.getenv("CODEBUILD_SRC_DIR"),product_template),
            "--s3-bucket", os.getenv("ARTIFACT_BUCKET"),
            "--s3-prefix", "resource/{}/{}".format(conf_product['name'],product_version),
            "--output-template-file", "product-{}-{}.yml".format(conf_product['name'],product_version),
        ],
        check=True, # if failed, stop the script immediately, prevent the sub sequence command to run  
        )
        logger.info("Created Cloudformation package for Cloudformation template {}".format(product_template))
        # copy packaged cfn template to s3 artifact bucket
        s3_client.upload_file("product-{}-{}.yml".format(conf_product['name'],product_version), os.getenv("ARTIFACT_BUCKET"), "product-{}-{}.yml".format(conf_product['name'],product_version),ExtraArgs={"ServerSideEncryption": "AES256"})
        ## create product
        LoadTemplateFromURL = 'https://{}.s3.{}.amazonaws.com/product-{}-{}.yml'.format(os.getenv("ARTIFACT_BUCKET"),os.getenv("AWS_REGION"),conf_product['name'],product_version)
        logger.info("Uploaded the Cloudformation package to S3 url {}".format(LoadTemplateFromURL))
        response_create_product=servicecatalog_client.create_product(
            Name=conf_product['name'],
            Owner=conf_product['owner'],
            Description=conf_product['description'],
            ProductType='CLOUD_FORMATION_TEMPLATE',
            Tags=Convert_Key_Value(conf_product['stack_tags']),
            ProvisioningArtifactParameters={
                'Name': product_version,
                'Description': conf_product['description'],
                'Info': {
                    'LoadTemplateFromURL': 'https://{}.s3.{}.amazonaws.com/product-{}-{}.yml'.format(os.getenv("ARTIFACT_BUCKET"),os.getenv("AWS_REGION"),conf_product['name'],product_version)
                },
                'Type': 'CLOUD_FORMATION_TEMPLATE',
                'DisableTemplateValidation': False
            }
        )
        if response_create_product is None:
            exitcode = 1
            logger.info("Failed to create product {}".format(conf_product['name']))
        else:                             
            product_id = response_create_product['ProductViewDetail']['ProductViewSummary']['ProductId']
            logger.info("Created product {} , {}".format(conf_product['name'],product_id))
            product_first_version = response_create_product['ProvisioningArtifactDetail']['Id']
            # Add ssm product id parameter
            ssm_client.put_parameter(Name='{}/{}/product-id'.format(SSM_PRODUCT_PREFIX,conf_product['name']),
                                    Value=product_id,
                                    Type='String',
                                    Overwrite=True)       
            logger.info('Created SSM parameter {}/{}/product-id with value {}'.format(SSM_PRODUCT_PREFIX,conf_product['name'],product_id))
            # Add ssm product version id parameter
            ssm_client.put_parameter(Name='{}/{}/{}/product-version-id'.format(SSM_PRODUCT_PREFIX,conf_product['name'],product_version),
                                    Value=response_create_product['ProvisioningArtifactDetail']['Id'],
                                    Type='String',
                                    Overwrite=True)       
            logger.info('Created SSM parameter {}/{}/{}/product-version-id with value {}'.format(SSM_PRODUCT_PREFIX,conf_product['name'],product_version,response_create_product['ProvisioningArtifactDetail']['Id']))
            # Collect the portfolio associations from config file and pass them as PortfolioIds parameter to product pipeline stack
            if "portfolio_associations" in conf_product:
                for portfolio_name in conf_product['portfolio_associations']:
                    logger.info("Getting ssm parameter {}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,portfolio_name))
                    portfolio_id = ssm_client.get_parameter(Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,portfolio_name),WithDecryption=True)['Parameter']['Value']
                    servicecatalog_client.associate_product_with_portfolio(
                        ProductId=product_id,
                        PortfolioId=portfolio_id
                    )
                    if conf_product['name'] == 'Bootstrapping-Admin-Product':
                        LocalRoleName = os.getenv("ServiceCatalogAdminRole")
                    else:
                        LocalRoleName = conf_product['launch_contraint_role']

                    logger.info("Creating contraint for product {} with role {} in portfolio {}".format(conf_product['name'],LocalRoleName,portfolio_name))
                    
                    if "arn:aws:iam:::role/" in LocalRoleName:                      
                        servicecatalog_client.create_constraint(
                            PortfolioId=portfolio_id,
                            ProductId=product_id,
                            Parameters= '{{"LocalRoleName":"{0}"}}'.format(LocalRoleName.lstrip("arn:aws:iam:::role/")),
                            Type='LAUNCH',
                            IdempotencyToken = product_id
                        )
                    else:
                        servicecatalog_client.create_constraint(
                            PortfolioId=portfolio_id,
                            ProductId=product_id,
                            Parameters= '{{"RoleArn":"{0}"}}'.format(LocalRoleName),
                            Type='LAUNCH',
                            IdempotencyToken = product_id
                        )
                        
                    logger.info("Created constraints for product {}".format(product_id))
    else:  #product exists
        # get product id from ssm
        tmp1_product_id = ssm_client.get_parameter(Name="{}/{}/product-id".format(SSM_PRODUCT_PREFIX,conf_product['name']),WithDecryption=True)['Parameter']['Value']
        tmp1_product_version = product_config_file_json['ProductVersionName']
        tmp1_product_template = product_config_file_json['Properties']['TemplateFilePath']
        tmp1_product_version_description = product_config_file_json['ProductVersionDescription']
        # Update product description if not match
        # Update product owner if not match
        # Update product tags if they don't match
        servicecatalog_client.update_product(Id=tmp1_product_id,Description=conf_product['description'],Owner=conf_product['owner'])
        #Create, Update and Delete Service Catalog Portfolio Tags
       
        # if config product version doesn't exist
        try:
            ssm_client.get_parameter(Name="{}/{}/{}/product-version-id".format(SSM_PRODUCT_PREFIX,conf_product['name'],tmp1_product_version),WithDecryption=True)['Parameter']['Value']
        except ssm_client.exceptions.ParameterNotFound:
            # cloudformation package
            subprocess.run(
            [
                "aws", "cloudformation", "package",
                "--region", os.getenv("AWS_REGION"),
                "--template-file", "{}/{}".format(os.getenv("CODEBUILD_SRC_DIR"),tmp1_product_template),
                "--s3-bucket", os.getenv("ARTIFACT_BUCKET"),
                "--s3-prefix", "resource/{}/{}".format(conf_product['name'],tmp1_product_version),
                "--output-template-file", "product-{}-{}.yml".format(conf_product['name'],tmp1_product_version)
            ],
            check=True, # if failed, stop the script immediately, prevent the sub sequence command to run
            )
            logger.info("Created Cloudformation package for Cloudformation template {}".format(tmp1_product_template))
            #copy packaged cfn template to s3 artifact bucket
            s3_client.upload_file("product-{}-{}.yml".format(conf_product['name'],tmp1_product_version), os.getenv("ARTIFACT_BUCKET"), "product-{}-{}.yml".format(conf_product['name'],product_version),ExtraArgs={"ServerSideEncryption": "AES256"})
            tmp1_LoadTemplateFromURL = 'https://{}.s3.{}.amazonaws.com/product-{}-{}.yml'.format(os.getenv("ARTIFACT_BUCKET"),os.getenv("AWS_REGION"),conf_product['name'],product_version)
            logger.info("Uploaded the Cloudformation package to S3 url {}".format(tmp1_LoadTemplateFromURL))
            #Add product version
            response_create_provisioning_artifact = servicecatalog_client.create_provisioning_artifact(
                ProductId= tmp1_product_id,
                Parameters={
                    'Name': tmp1_product_version,
                    'Description': tmp1_product_version_description,
                    'Info': {
                        'LoadTemplateFromURL': 'https://{}.s3.amazonaws.com/product-{}-{}.yml'.format(os.getenv("ARTIFACT_BUCKET"),conf_product['name'],tmp1_product_version),
                    },
                    'Type': 'CLOUD_FORMATION_TEMPLATE'
                }
            )
            logger.info("Created provisioning artifact for product {}".format(tmp1_product_id))
            ssm_client.put_parameter(Name='{}/{}/{}/product-version-id'.format(SSM_PRODUCT_PREFIX,conf_product['name'],tmp1_product_version),
                                    Value=response_create_provisioning_artifact['ProvisioningArtifactDetail']['Id'],
                                    Type='String',
                                    Overwrite=True)       
            logger.info('Created SSM parameter {}/{}/{}/product-version-id'.format(SSM_PRODUCT_PREFIX,conf_product['name'],tmp1_product_version))

    if conf_product['name'] == "Bootstrapping-Admin-Product":
        BP_PRODUCT_VERSION = product_config_file_json['ProductVersionName']
        BP_PRODUCT_ID = ssm_client.get_parameter(Name="{}/{}/product-id".format(SSM_PRODUCT_PREFIX,conf_product['name']),WithDecryption=True)['Parameter']['Value']
        ssm_client.put_parameter(Name='{}/{}/latestversion'.format(SSM_PRODUCT_PREFIX,conf_product['name']),
                                Value=product_config_file_json['ProductVersionName'],
                                Type='String',
                                Overwrite=True)       
        logger.info('Created SSM parameter {}/{}/latestversion pointing to version {}'.format(SSM_PRODUCT_PREFIX,conf_product['name'],product_config_file_json['ProductVersionName']))
        
    else:
        #Provision or update provisioned product for all Admin products
        PRODUCT_ID = ssm_client.get_parameter(Name="{}/{}/product-id".format(SSM_PRODUCT_PREFIX,conf_product['name']),WithDecryption=True)['Parameter']['Value']
        time.sleep(10)
        respose_describe_product = servicecatalog_client.describe_product(Id=PRODUCT_ID)
        launch_path_id = respose_describe_product['LaunchPaths'][0]['Id']
        response_search_provisioned_products = servicecatalog_client.search_provisioned_products(
            Filters={
                'SearchQuery': [
                    '{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')),
                ]
            }
        )
        if response_search_provisioned_products['ProvisionedProducts'] == []:
            response_provision_product = servicecatalog_client.provision_product(
                ProductId = PRODUCT_ID,
                ProvisioningArtifactName= respose_describe_product['ProvisioningArtifacts'][0]['Name'],
                PathId = launch_path_id,
                ProvisionedProductName = '{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')),
                Tags = Product_Tags
            )
            provisioned_product_id = response_provision_product['RecordDetail']['ProvisionedProductId']
            logger.info('Provisioned product {} {}'.format(conf_product['name'],provisioned_product_id))
            # Checking for Errors during the provisioning of Blueprint product pipeline
            while True:
                logger.info('Sleeping for 10 seconds')
                time.sleep(10)
                response_describe_provision_product = servicecatalog_client.describe_provisioned_product(Name='{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
                if response_describe_provision_product['ProvisionedProductDetail']['Status'] == "AVAILABLE":
                    ssm_client.put_parameter(Name='{}/{}/provisioned-product-id'.format(SSM_PRODUCT_PREFIX,conf_product['name']),
                                            Value=response_describe_provision_product['ProvisionedProductDetail']['Id'],
                                            Type='String',
                                            Overwrite=True)       
                    logger.info('Created provisioned product {} now is available'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                    logger.info('Created SSM parameter {}/{}/provisioned-product-id'.format(SSM_PRODUCT_PREFIX,conf_product['name']))
                    break
                elif response_describe_provision_product['ProvisionedProductDetail']['Status'] == "ERROR":
                    servicecatalog_client.terminate_provisioned_product(ProvisionedProductName='{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
                    EXITCODE = 1
                    logger.error('Created provisioned product {} is in ERROR state'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                    break
        else:
            if respose_describe_product['ProvisioningArtifacts'][0]['Name'] == product_config_file_json['ProductVersionName']:
                logger.info("Product version/provisioningArtifact {} for provisioned product {}-{}-{} exists".format(respose_describe_product['ProvisioningArtifacts'][0]['Name'],conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
                continue
            else:                
                servicecatalog_client.update_provisioned_product(
                    ProductId = PRODUCT_ID,
                    ProvisioningArtifactName= product_config_file_json['ProductVersionName'],
                    PathId = launch_path_id,
                    ProvisionedProductName = '{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')),
                    Tags = Product_Tags
                )
                while True:
                    logger.info('Sleeping for 10 seconds')
                    time.sleep(10)
                    response_describe_provision_product = servicecatalog_client.describe_provisioned_product(Name='{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
                    if response_describe_provision_product['ProvisionedProductDetail']['Status'] == "AVAILABLE":
                        logger.info('Updated provisioned product {} now is available'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                        break
                    elif response_describe_provision_product['ProvisionedProductDetail']['Status'] == "ERROR" or response_describe_provision_product['ProvisionedProductDetail']['Status'] == "TAINTED":
                        servicecatalog_client.terminate_provisioned_product(ProvisionedProductName='{}-{}-{}'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
                        EXITCODE = 1
                        logger.error('Update provisioned product {} is in ERROR state'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                        break            
            

logger.info("Buildspec create admin product job exit code is {}".format(exitcode))
exit(exitcode)