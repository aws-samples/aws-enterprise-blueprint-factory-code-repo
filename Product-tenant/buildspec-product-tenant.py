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

exitcode = 0

def Convert_Key_Value(key_value_dict):
    key_value_list = []
    for k,v in key_value_dict.iteritems():
        key_value_list.append({'Key': k,'Value': v})
    return key_value_list

session = boto3.Session()

ssm_client = session.client('ssm')
servicecatalog_client = session.client('servicecatalog')
s3_client = boto3.client('s3',region_name=os.getenv("AWS_REGION"))
cfb_client = boto3.client('cloudformation')


SSM_PRODUCT_PREFIX = "/blueprints/service-catalog/{}/AdminProduct".format(os.getenv('ENVIRONMENTTYPE'))
SSM_PORTFOLIO_PREFIX="/blueprints/service-catalog/{}/Portfolio".format(os.getenv('ENVIRONMENTTYPE'))
SSM_BLUEPRINT_PREFIX="/blueprints/service-catalog/{}/BlueprintProduct".format(os.getenv('ENVIRONMENTTYPE'))


CONFIG_FILE_DEV = "{}/bp_config.yml".format(os.getenv("CODEBUILD_SRC_DIR_ConfigOutput"))
PRODUCT_FILE_DEV = "{}/bp_config.yml".format(os.getenv("CODEBUILD_SRC_DIR_BlueprintProductOutput"))



with open(CONFIG_FILE_DEV, "r") as config_file_dev:
    config_file_dev_json = yaml.safe_load(config_file_dev)

if config_file_dev_json is not None and 'products' in config_file_dev_json and config_file_dev_json['products'] is not None:
    conf_product_dev_list = config_file_dev_json['products']
    conf_product_dev_names = [d['name'] for d in conf_product_dev_list if 'name' in d]
else:
    conf_product_dev_list = []
    conf_product_dev_names = []

if config_file_dev_json is not None and 'portfolios' in config_file_dev_json and config_file_dev_json['portfolios'] is not None:
    conf_portfolio_dev_list = config_file_dev_json['portfolios']
    conf_portfolio_dev_names = [d['name'] for d in conf_portfolio_dev_list if 'name' in d]
else:
    conf_portfolio_dev_list = []
    conf_portfolio_dev_names = []

## Iterate through different products mentioned in the config file
for conf_product in conf_product_dev_list:
   
    BLUEPRINT_PRODUCT_ID = ssm_client.get_parameter(Name="/blueprints/service-catalog/{}/AdminProduct/Bootstrapping-Admin-Product/product-id".format(os.getenv('ENVIRONMENTTYPE'),WithDecryption=True))['Parameter']['Value']
    PROVISIONING_ARTIFACT_NAME = ssm_client.get_parameter(Name="/blueprints/service-catalog/{}/AdminProduct/Bootstrapping-Admin-Product/latestversion".format(os.getenv('ENVIRONMENTTYPE'),WithDecryption=True))['Parameter']['Value']                                                              

    ## Checks for any existing product with the same name. If not, it'll create a new one, else, update the existing one with new package
    ProductConfigFile = "{}/{}".format(os.getenv("CODEBUILD_SRC_DIR_BlueprintProductOutput"), conf_product['product_config_file'])
    with open(ProductConfigFile, "r") as product_config_file:
        product_config_file_json = yaml.safe_load(product_config_file)        
    
    product_template = product_config_file_json['Properties']['TemplateFilePath']
    product_version = product_config_file_json['ProductVersionName']

    conf_product_stack_tags = conf_product['stack_tags'] if 'stack_tags' in conf_product else ''
    
    Product_Tags = []
    for tag in conf_product_stack_tags:
        Product_Tags.append({'Key': tag, 'Value': conf_product_stack_tags[tag]})
 
    PortfolioIds = []
    ## Collect the portfolio associations from config file and pass them as PortfolioIds parameter to product pipeline stack
    if "portfolio_associations" in conf_product:
        for portfolio_name in conf_product['portfolio_associations']:
            portfolio_id = ssm_client.get_parameter(Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,portfolio_name),WithDecryption=True)['Parameter']['Value']
            PortfolioIds.append(portfolio_id)       
    
    PortfolioIds_string = ','.join(PortfolioIds)
    
    conf_product_description = conf_product['description'] if 'description' in conf_product else ''
    
   
    ProvisioningParameters = [
        {'Key':"ProductName", "Value":conf_product['name']},
        {'Key':"ProductDescription", "Value":conf_product_description },
        {'Key':"ProductConfigFile","Value":conf_product['product_config_file'] },
        {'Key':"ProductOwner","Value":conf_product['owner']},
        {'Key':"CodeCommitBranchName", "Value":"main"},        
#        {'Key':"VersionStrategy","Value":conf_product},
#        {'Key':"EnvironmentType","Value":os.getenv('ENVIRONMENTTYPE')},
        {'Key':"PortfolioIds","Value":PortfolioIds_string}
    ]

    logger.info("PortfolioIds : {}".format(PortfolioIds_string))
    response_search_provisioned_products = servicecatalog_client.search_provisioned_products(
                                        Filters={
                                        'SearchQuery': [
                                            '{}-{}-{}-pipeline'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')),
                                        ]
                                    }
                                )
    respose_describe_product = servicecatalog_client.describe_product(Id=BLUEPRINT_PRODUCT_ID)
    launch_path_id = respose_describe_product['LaunchPaths'][0]['Id'] 

    if response_search_provisioned_products['ProvisionedProducts'] == []:
        
        logger.info(ProvisioningParameters)
        response_provision_product = servicecatalog_client.provision_product(
                ProductId = BLUEPRINT_PRODUCT_ID,
                ProvisioningArtifactName= PROVISIONING_ARTIFACT_NAME,
                PathId = launch_path_id,
                ProvisioningParameters = ProvisioningParameters,
                ProvisionedProductName = '{}-{}-{}-pipeline'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')),
                Tags = Product_Tags
            )
        provisioned_product_id = response_provision_product['RecordDetail']['ProvisionedProductId']
        logger.info('Provisioning product {} {}'.format(conf_product['name'],provisioned_product_id))
        ## Checking for Errors during the provisioning of Blueprint product pipeline
        while True:
            logger.info('Sleeping for 10 seconds')
            time.sleep(10)
            response_describe_provision_product = servicecatalog_client.describe_provisioned_product(Name='{}-{}-{}-pipeline'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
            if response_describe_provision_product['ProvisionedProductDetail']['Status'] == "AVAILABLE":
                ssm_client.put_parameter(Name='{}/{}/provisioned-product-id'.format(SSM_BLUEPRINT_PREFIX,conf_product['name']),
                                        Value=response_describe_provision_product['ProvisionedProductDetail']['Id'],
                                        Type='String',
                                        Overwrite=True)       
                logger.info('Created provisioned product {} now is available'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                logger.info('Created SSM parameter {}/{}/provisioned-product-id'.format(SSM_BLUEPRINT_PREFIX,conf_product['name']))
                
                if "launch_contraint_role" in conf_product and conf_product['launch_contraint_role'] is not None:
                    while True:
                        time.sleep(10)
                        try:
                            product_id = ssm_client.get_parameter(Name="{}/{}/product-id".format(SSM_BLUEPRINT_PREFIX,conf_product['name']),WithDecryption=True)['Parameter']['Value'] 
                        except SSM.Client.exceptions.ParameterNotFound:
                            continue
                        break
                    
                    logger.info('Creating launch constraint role for portfolio id {} product id {} for product {}'.format(portfolio_id,product_id,conf_product['name']))
                    
                    for portfolio_id in PortfolioIds:
                        if "arn:aws:iam:::role/" in conf_product['launch_contraint_role']:                      
                            servicecatalog_client.create_constraint(
                                PortfolioId=portfolio_id,
                                ProductId=product_id,
                                Parameters= '{{"LocalRoleName":"{0}"}}'.format(conf_product['launch_contraint_role'].lstrip("arn:aws:iam:::role/")),
                                Type='LAUNCH',
                                IdempotencyToken = product_id
                            )
                        else:
                            servicecatalog_client.create_constraint(
                                PortfolioId=portfolio_id,
                                ProductId=product_id,
                                Parameters= '{{"RoleArn":"{0}"}}'.format(conf_product['launch_contraint_role']),
                                Type='LAUNCH',
                                IdempotencyToken = product_id
                            )
                        logger.info("Created constraint role {} for product {}".format(conf_product['launch_contraint_role'],product_id))
                
                break
            elif response_describe_provision_product['ProvisionedProductDetail']['Status'] == "ERROR":
                EXITCODE = 1
                logger.error('Created provisioned product {} is in ERROR state'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                break
    else:
        servicecatalog_client.update_provisioned_product(
            ProductId = BLUEPRINT_PRODUCT_ID,
            ProvisioningArtifactName= PROVISIONING_ARTIFACT_NAME,
            PathId = launch_path_id,
            ProvisioningParameters = ProvisioningParameters,
            ProvisionedProductName = '{}-{}-{}-pipeline'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')),
            Tags = Product_Tags
        )
        while True:
            logger.info('Sleeping for 10 seconds')
            time.sleep(10)
            response_describe_provision_product = servicecatalog_client.describe_provisioned_product(Name='{}-{}-{}-pipeline'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
            if response_describe_provision_product['ProvisionedProductDetail']['Status'] == "AVAILABLE":
                logger.info('Updated provisioned product {} now is available'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                break
            elif response_describe_provision_product['ProvisionedProductDetail']['Status'] == "ERROR" or response_describe_provision_product['ProvisionedProductDetail']['Status'] == "TAINTED":
                servicecatalog_client.terminate_provisioned_product(ProvisionedProductName='{}-{}-{}-pipeline'.format(conf_product['name'],os.getenv('ENVIRONMENTTYPE'),os.getenv('AWS_ACCOUNT_ID')))
                EXITCODE = 1
                logger.error('Update provisioned product {} is in ERROR state'.format(response_describe_provision_product['ProvisionedProductDetail']['Id']))
                break            
      
            
#### Delete Product if it doesn't exist in config_products.yaml ###
 ## List all ssm product parameters by path  (product-name)/(version)/product-id
response_get_parameters_by_path = ssm_client.get_parameters_by_path(Path=SSM_BLUEPRINT_PREFIX,Recursive=True)
ssm_parameters_products = [d['Name'].split('/')[-2] for d in response_get_parameters_by_path['Parameters'] if 'Name' in d and d['Name'].endswith("/product-id") ]
#config_products = conf_product_dev_names

for ssm_product in ssm_parameters_products:
    ## 	if product name doesn't exist in config
    if ssm_product not in conf_product_dev_names:
        ssm_product_id = ssm_client.get_parameter(Name="{}/{}/product-id".format(SSM_BLUEPRINT_PREFIX,ssm_product),WithDecryption=True)['Parameter']['Value']
        ## list existing portfolios for the product
        existing_product_associations = servicecatalog_client.list_portfolios_for_product(ProductId=ssm_product_id)['PortfolioDetails']
        for delete_product_associate in existing_product_associations:
            ## disassociate product from Portfolio
            #delete_portfolio_id = ssm_client.get_parameter(Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,delete_product_associate['DisplayName']),WithDecryption=True)['Parameter']['Value']
            servicecatalog_client.disassociate_product_from_portfolio(ProductId=ssm_product_id,PortfolioId=delete_product_associate['Id'])
            logger.info('Disassociating product {} from portfolio {}'.format(ssm_product_id,delete_product_associate['Id']))
        ## terminate provisioned product for the product
        logger.info("Terminating {}".format(ssm_product))
        delete_provionsioned_product_id = ssm_client.get_parameter(Name="{}/{}/provisioned-product-id".format(SSM_BLUEPRINT_PREFIX,ssm_product),WithDecryption=True)['Parameter']['Value']
        servicecatalog_client.terminate_provisioned_product(ProvisionedProductId=delete_provionsioned_product_id)
        logger.info('Terminated provisioned product {}'.format(delete_provionsioned_product_id))
        ssm_client.delete_parameter(Name="{}/{}/provisioned-product-id".format(SSM_BLUEPRINT_PREFIX,ssm_product))            
        logger.info('Deleted SSM parameter {}/{}/provisioned-product-id'.format(SSM_BLUEPRINT_PREFIX,ssm_product))
        ## delete product(id)
        servicecatalog_client.delete_product(Id=ssm_product_id )
        logger.info("Deleted product {}".format(ssm_product_id))
        ## remove product name from ssm
        response_product_ssm_parameters = ssm_client.get_parameters_by_path(Path="{}/{}".format(SSM_BLUEPRINT_PREFIX,ssm_product),Recursive=True)
        for ssm_parameter in response_product_ssm_parameters['Parameters']:
            ssm_client.delete_parameter(Name=ssm_parameter['Name'])
            logger.info("Deleted SSM parameter {}".format(ssm_parameter['Name']))
        ## Remove portfolios

logger.info("Buildspec create tenant product job exit code is {}".format(exitcode))
exit(exitcode)        
