import yaml
import boto3
import logging
import json
import os

log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(format = log_format, level = logging.INFO)
logger = logging.getLogger()

exitcode = 0

# if os.getenv("CODEBUILD_SRC_DIR_ConfigOutput") is None:
#     os.environ["BP_PRODUCT_VERSION"] = "0.8.6"
#     os.environ["PROVISIONING_ARTIFACT_NAME"] = "0.8.6"
#     os.environ["PRODUCT_ID"] = "prod-b4k6ytqwxswni"
#     os.environ["BLUEPRINT_PRODUCT_ID"] = "prod-b4k6ytqwxswni"
#     os.environ["ENVIRONMENTTYPE"] = "DEV"
#     os.environ["CODEBUILD_SRC_DIR_ConfigOutput"] = "c:\\Amazon\Blogs\\test9\\ServiceCatalog-ConfigRepo"
#     os.environ["ARTIFACT_BUCKET"] =  "scd-joeguo-service-catalog"
#     os.environ["AWS_REGION"] = "ap-southeast-2"
#     os.environ["LocalRoleName"] = "LocalRoleName"
#     os.environ["AWS_ACCOUNT_ID"] = "831932040055"

CONFIG_FILE_DEV = "{}/bp_config.yml".format(os.getenv("CODEBUILD_SRC_DIR_ConfigOutput"))

SSM_PRODUCT_PREFIX = "/blueprints/service-catalog/{}/AdminProduct".format(os.getenv('ENVIRONMENTTYPE'))
SSM_PORTFOLIO_PREFIX="/blueprints/service-catalog/{}/Portfolio".format(os.getenv('ENVIRONMENTTYPE'))

def extract_portfolio_tags(stack_tags):
    tags = []
    for tag in stack_tags:
        tags.append({'Key': tag,'Value': stack_tags[tag]})
    return tags

session = boto3.Session()

ssm_client = session.client('ssm')
servicecatalog_client = session.client('servicecatalog')

with open(CONFIG_FILE_DEV, "r") as config_file:
    config_json = yaml.safe_load(config_file)


config_admin_json = {
  "portfolios": [
    {
      "portfolio_name": "Bootstrapping-Admin-Portfolio",
      "owner": "ServiceCatalogAdminTeam",
      "provider_name": "AWS",
      "description": "Bootstrapping admin products portfolio",
      "stack_tags": {
        "DataClassification": "Confidential",
        "Organization": "AWS",
        "ProductType": "Admin"
      }
    }
  ]
}


if config_admin_json is None or 'portfolios' not in config_admin_json or config_admin_json['portfolios'] is None:
    config_admin_json_list = []
else:
    config_admin_json_list = config_admin_json['portfolios']

if config_json is None or 'portfolios' not in config_json or config_json['portfolios'] is None:
    config_json_list = []
else:
    config_json_list = config_json['portfolios']

conf_portfolio_list = config_admin_json_list + config_json_list

conf_portfolio_names = [d['portfolio_name'] for d in conf_portfolio_list if 'portfolio_name' in d]
sc_portfolio_list = servicecatalog_client.list_portfolios()["PortfolioDetails"]
sc_portfolio_names = [d['DisplayName'] for d in sc_portfolio_list if 'DisplayName' in d]




## Iterate through the portfolios mentioned in config file and collect relevant details
## Check if a particular portfolio exists and create if it doesn't   
for conf_portfolio in conf_portfolio_list:
    conf_portfolio_name = conf_portfolio['portfolio_name']
    conf_portfolio_description = conf_portfolio['description'] if 'description' in conf_portfolio else ''
    conf_portfolio_stack_tags = conf_portfolio['stack_tags'] if 'stack_tags' in conf_portfolio else ''
    conf_portfolio_provider_name = conf_portfolio['provider_name'] if 'provider_name' in conf_portfolio else 'AWS'

    if conf_portfolio_name not in sc_portfolio_names:
        result = servicecatalog_client.create_portfolio(
                    DisplayName=conf_portfolio_name,
                    Description=conf_portfolio_description,
                    ProviderName=conf_portfolio_provider_name,
                    Tags=extract_portfolio_tags(conf_portfolio_stack_tags)
                    )
        sc_portfolio_id = result['PortfolioDetail']['Id']
        logger.info("Created portfolio {} , {}".format(conf_portfolio_name , sc_portfolio_id))
        ssm_client.put_parameter(
            Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,conf_portfolio_name),
            Description='blueprint_publish_pipeline_portfolio_id',
            Value=sc_portfolio_id,
            Type='String',
            Overwrite=True            
            )    
        ssm_client.put_parameter(
            Name="{}/{}".format(SSM_PORTFOLIO_PREFIX,conf_portfolio_name),
            Description='blueprint_publish_pipeline_portfolio_id',
            Value=sc_portfolio_id,
            Type='String',
            Overwrite=True            
            )    
        logger.info("Created SSM parameter {}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,conf_portfolio_name))
    else:
        ## If portfolio exists, update the portfolio
        ssm_portfolio_id = ssm_client.get_parameter(Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,conf_portfolio_name),WithDecryption=True)["Parameter"]["Value"]
        existing_tags = servicecatalog_client.describe_portfolio(Id=ssm_portfolio_id)['Tags']
        existing_tags_keys = [d['Key'] for d in existing_tags if 'Key' in d]
        servicecatalog_client.update_portfolio(
            Id=ssm_portfolio_id, 
            DisplayName=conf_portfolio_name, 
            Description=conf_portfolio_description, 
            ProviderName=conf_portfolio_provider_name, 
            AddTags=extract_portfolio_tags(conf_portfolio_stack_tags),
            RemoveTags=existing_tags_keys
            )
        logger.info("Updated portfolio {} , {}".format(conf_portfolio_name,ssm_portfolio_id))

portfolios_to_be_deleted = []

for sc_portfolio in sc_portfolio_list:
    ## Deletion process for portfolio if its not present in the Config File
    if sc_portfolio['DisplayName'] in conf_portfolio_names:
        logger.info("Portfolio {} exists in config file".format(sc_portfolio['DisplayName']))
    else:
        if sc_portfolio['DisplayName'] == "Bootstrapping-Admin-Portfolio":
            continue
        logger.info("Portfolio {} doesn't exist in config file. It'll be removed".format(sc_portfolio['DisplayName']))
        ## Check is there is any product associated with portfolio. If yes, abort the deletion
        sc_portfolio_product_list = servicecatalog_client.search_products_as_admin(PortfolioId=sc_portfolio['Id'])
        if sc_portfolio_product_list["ProductViewDetails"]:
            logger.error("Portfolio {} has product associations. Aborting portfolio removal. Please delete all associated products.".format(sc_portfolio['DisplayName']))
        else:
            portfolios_to_be_deleted.append(sc_portfolio)

for portfolio in portfolios_to_be_deleted:
    ## Delete portfolio-shares of the portfolio
#    servicecatalog_client.delete_portfolio_share(PortfolioId=portfolio['Id'],OrganizationNode={'Type':'ORGANIZATION','Value':os.getenv('ORGUNITID')})
#    logger.info("Deleted portfolio-share (ORGANIZATION {}) for Portfolio {}".format(os.getenv('ORGUNITID'),portfolio['DisplayName']))
    portfolio_sharedAccounts_list = servicecatalog_client.describe_portfolio_shares(PortfolioId=portfolio['Id'],Type='ACCOUNT')
    for portfolio_share_acc in portfolio_sharedAccounts_list['PortfolioShareDetails']:
        servicecatalog_client.delete_portfolio_share(PortfolioId=portfolio['Id'],AccountId=portfolio_share_acc['PrincipalId'])
        logger.info("Deleted portfolio-share ACCOUNT {} for Portfolio {}".format(portfolio_share_acc['PrincipalId'],portfolio['DisplayName']))
    portfolio_sharedOrg_list = servicecatalog_client.describe_portfolio_shares(PortfolioId=portfolio['Id'],Type='ORGANIZATION')
    for portfolio_sharedOrg_acc in portfolio_sharedOrg_list['PortfolioShareDetails']:
        servicecatalog_client.delete_portfolio_share(PortfolioId=portfolio['Id'],OrganizationNode = {'Type': 'ORGANIZATION','Value': portfolio_sharedOrg_acc['PrincipalId']})
        logger.info("Deleted portfolio-share Org {} for Portfolio {}".format(portfolio_sharedOrg_acc['PrincipalId'],portfolio['DisplayName']))
    ## Disassociate IAM Principals associated with the portfolio
    portfolio_principals_list =  servicecatalog_client.list_principals_for_portfolio(PortfolioId=portfolio['Id'])['Principals']
    for principal in portfolio_principals_list:
        if "arn:aws:iam:::role" in principal['PrincipalARN']:
            servicecatalog_client.disassociate_principal_from_portfolio(PortfolioId=portfolio['Id'],PrincipalARN=principal['PrincipalARN'],PrincipalType='IAM_PATTERN')
        else:
            servicecatalog_client.disassociate_principal_from_portfolio(PortfolioId=portfolio['Id'],PrincipalARN=principal['PrincipalARN'],PrincipalType='IAM')
        logger.info("Disassociated princiapl {} from portfolio {}".format(principal['PrincipalARN'],portfolio['DisplayName']))
    ## Disassociate Tag Options associated with the portfolio        
    portfolio_tagOptions_list = servicecatalog_client.describe_portfolio(Id=portfolio['Id'])['TagOptions']
    for tagOption in portfolio_tagOptions_list:
        servicecatalog_client.disassociate_tag_option_from_resource(ResourceId=portfolio['Id'],TagOptionId=tagOption['Id'])
        logger.info("Disassociated tag option {} from portfolio {}".format(tagOption['Id'],portfolio['DisplayName']))
     
    ## Delete the SSM Parameter that stores the portfolio Id
    try:
        portfolio_ssm_parameter = ssm_client.get_parameter(Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,portfolio['DisplayName']),WithDecryption=True)
        ssm_client.delete_parameter(Name="{}/{}".format(SSM_PORTFOLIO_PREFIX,portfolio['DisplayName']))
        ssm_client.delete_parameter(Name=portfolio_ssm_parameter['Parameter']['Name'])
        logger.info("Deleted SSM Parameter {} for Portfolio {}".format(portfolio_ssm_parameter['Parameter']['Name'],portfolio['DisplayName']))
    except ssm_client.exceptions.ParameterNotFound:
        logger.info("Portfolio-id parameter doesn't exist for {}".format(portfolio['DisplayName']))
    
    ## Delete the portfolio
    servicecatalog_client.delete_portfolio(Id=portfolio['Id'])
    logger.info("Deleted porfolio {} , {}".format(portfolio['DisplayName'],portfolio['Id']))

## Delete unused Tag Options    
tagOptions_list = servicecatalog_client.list_tag_options()['TagOptionDetails']
for tagOption in tagOptions_list:
    if tagOption['Active'] == False:
        servicecatalog_client.delete_tag_option(Id=tagOption['Id'])
        logger.info("Deleted inactive tag option {}".format(tagOption['Id']))

logger.info("Buildspec create portfolio job exit code is {}".format(exitcode))
exit(exitcode)