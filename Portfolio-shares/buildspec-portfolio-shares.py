import yaml
import boto3
from botocore.exceptions import ClientError
import logging
import json
import os

log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
logging.basicConfig(format = log_format, level = logging.INFO)
logger = logging.getLogger()

exitcode = 0

CONFIG_FILE_DEV = "{}/bp_config.yml".format(os.getenv("CODEBUILD_SRC_DIR_ConfigOutput"))

SSM_PRODUCT_PREFIX = "/blueprints/service-catalog/{}/AdminProduct".format(os.getenv('ENVIRONMENTTYPE'))
SSM_PORTFOLIO_PREFIX="/blueprints/service-catalog/{}/Portfolio".format(os.getenv('ENVIRONMENTTYPE'))

session = boto3.Session()

ssm_client = session.client('ssm')
servicecatalog_client = session.client('servicecatalog')

with open(CONFIG_FILE_DEV, "r") as config_file_dev:
    config_file_dev_json = yaml.safe_load(config_file_dev)
   
if config_file_dev_json is None or 'portfolios' not in config_file_dev_json or config_file_dev_json['portfolios'] is None:
    conf_portfolio_list = []
else:
    conf_portfolio_list = config_file_dev_json['portfolios']

for portfolio in conf_portfolio_list:
    logger.info("{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,portfolio['portfolio_name']))
    sc_portfolio_id = ssm_client.get_parameter(Name="{}/{}/portfolio-id".format(SSM_PORTFOLIO_PREFIX,portfolio['portfolio_name']),WithDecryption=True)['Parameter']['Value']
    #Get a list of the shares for existing portfolio to an array
    sc_portfolio_shares = servicecatalog_client.describe_portfolio_shares(PortfolioId=sc_portfolio_id,Type='ACCOUNT')['PortfolioShareDetails']
    sc_portfolio_shares_acc_id = [d['PrincipalId'] for d in sc_portfolio_shares if 'PrincipalId' in d]
    
    #Get a list of the shares for config file to an array
    # conf_portfolio_shares = portfolio['share_to_accounts']

    if portfolio is None:
        conf_portfolio_shares = []
    if portfolio is None or 'share_to_accounts' not in portfolio or portfolio['share_to_accounts'] is None:
        conf_portfolio_shares = []
    else:
        conf_portfolio_shares = portfolio['share_to_accounts']
        
    conf_portfolio_shares_acc_id = [d['account_id'] for d in conf_portfolio_shares if 'account_id' in d]
   
    #Iterate through existing shares, delete shares if not exist in config
    for sc_portfolio_share in sc_portfolio_shares:
        if sc_portfolio_share['PrincipalId'] not in conf_portfolio_shares_acc_id:
            try:
                servicecatalog_client.delete_portfolio_share(PortfolioId=sc_portfolio_id,AccountId=sc_portfolio_share['PrincipalId'])
                logger.info("Deleted Portfolio Share {} for portfolio {}".format(sc_portfolio_share['PrincipalId'],portfolio['portfolio_name']))
            except ClientError as e:
                logger.info("Delete Portfolio Share {} for portfolio {} failed with error {}".format(sc_portfolio_share['PrincipalId'],portfolio['portfolio_name'],e.message))
    #Iterate through shares in config, Create shares if not exist in existing portfolios
    for conf_portfolio_share in conf_portfolio_shares:
        if conf_portfolio_share['account_id'] not in sc_portfolio_shares_acc_id:
            try:
                servicecatalog_client.create_portfolio_share(PortfolioId=sc_portfolio_id,AccountId=conf_portfolio_share['account_id'])
                logger.info("Created Portfolio Share {} for portfolio {}".format(conf_portfolio_share['account_id'],portfolio['portfolio_name']))
            except ClientError as e:
                logger.info("Create Portfolio Share {} for portfolio {} failed with error {}".format(conf_portfolio_share['account_id'],portfolio['portfolio_name'],e.message))
    if 'share_to_ou' in portfolio:
        conf_portfolio_ou = portfolio['share_to_ou']
        if conf_portfolio_ou is None:
            conf_portfolio_ou = []
    else:
        conf_portfolio_ou = []
    if "share_to_ou" in portfolio:
        sc_portfolio_ou = servicecatalog_client.describe_portfolio_shares(PortfolioId=sc_portfolio_id,Type='ORGANIZATION')
        if sc_portfolio_ou is not None and 'PortfolioShareDetails' in sc_portfolio_ou:
            sc_portfolio_ou_id = [d['PrincipalId'] for d in sc_portfolio_ou['PortfolioShareDetails'] if 'PrincipalId' in d]
        else:
            sc_portfolio_ou_id = []

        for sc_ou_id in sc_portfolio_ou_id:
            if sc_ou_id not in conf_portfolio_ou:
                servicecatalog_client.delete_portfolio_share(PortfolioId=sc_portfolio_id,OrganizationNode={'Type':'ORGANIZATION','Value': sc_ou_id} )
                logger.info("Disassociated ou id {} with portfolio {}".format(sc_ou_id,portfolio['portfolio_name']))
        for conf_ou_id in conf_portfolio_ou:
            if conf_ou_id not in sc_portfolio_ou_id:
                org_arn = "arn:aws:organizations::{}:organization/{}".format(os.environ["AWS_ACCOUNT_ID"],conf_ou_id['org_id'])
                try:
                    servicecatalog_client.create_portfolio_share(PortfolioId=sc_portfolio_id,OrganizationNode={'Type':'ORGANIZATION','Value': org_arn}, SharePrincipals=True,ShareTagOptions=True )
                    logger.info("Associated ou id {} with portfolio {}".format(org_arn,portfolio['portfolio_name']))
                except ClientError as e:
                    logger.info("Failed to associate ou id {} with portfolio {}. Error message: {}".format(org_arn,portfolio['portfolio_name'],e))
   
    ### Create, Update and Delete Service Catalog Portfolio Tags
    if "stack_tags" in portfolio:
        sc_portfolio_tags = servicecatalog_client.describe_portfolio(Id=sc_portfolio_id)['Tags']
        sc_portfolio_tags_keys = [d['Key'] for d in sc_portfolio_tags if 'Key' in d]
        sc_portfolio_tags_values = [d['Value'] for d in sc_portfolio_tags if 'Value' in d]
        sc_portfolio_tags_dict = dict(zip(sc_portfolio_tags_keys,sc_portfolio_tags_values))
      
        conf_portfolio_tags = []
        conf_portfolio_tags_keys = []
        for tag in portfolio['stack_tags']:
            conf_portfolio_tags.append({'Key': tag, 'Value': portfolio["stack_tags"][tag]})
            conf_portfolio_tags_keys.append(tag)
        sc_portfolio_tag = {}            
        index = 0
        while index < len(sc_portfolio_tags):
            if sc_portfolio_tags[index] not in conf_portfolio_tags:
                try:
                    servicecatalog_client.update_portfolio(Id=sc_portfolio_id,RemoveTags=['{}'.format(sc_portfolio_tags[index]['Key'])])
                    logger.info("Removed tag {} for portfolio {}".format(sc_portfolio_tags[index]['Key'],portfolio['portfolio_name']))
                except ClientError as e:
                    logger.info("Failed to remove tag {} for portfolio {}".format(sc_portfolio_tags[index]['Key'],portfolio['portfolio_name']))
            index += 1            
        
        for conf_portfolio_tag_key,conf_portfolio_tag_value in portfolio["stack_tags"].items():
            if conf_portfolio_tag_key not in conf_portfolio_tags_keys:
                try:
                    servicecatalog_client.update_portfolio(Id=sc_portfolio_id,AddTags=[{'Key':conf_portfolio_tag_key,'Value':conf_portfolio_tag_value}])
                    logger.info("Added tag {} for portfolio {}".format(sc_portfolio_tags[index]['Key'],portfolio['portfolio_name']))
                except ClientError as e:
                    logger.info("Failed to add tag {} for portfolio {}".format(sc_portfolio_tags[index]['Key'],portfolio['portfolio_name']))
    ### Associate, Update and disassociate Service Catalog Portfolio Principals
    sc_portfolio_principals = servicecatalog_client.list_principals_for_portfolio(PortfolioId=sc_portfolio_id)['Principals']
    sc_portfolio_principal_ARN_list = [d['PrincipalARN'] for d in sc_portfolio_principals if 'PrincipalARN' in d]
    conf_portfolio_principals = portfolio['portfolio_access_roles']
    if conf_portfolio_principals is None:
        conf_portfolio_principals = []
    if "portfolio_access_roles" in portfolio:
        for portfolio_principal_ARN in sc_portfolio_principal_ARN_list:
            if portfolio_principal_ARN not in conf_portfolio_principals:
                servicecatalog_client.disassociate_principal_from_portfolio(PortfolioId=sc_portfolio_id,PrincipalARN=portfolio_principal_ARN)
                logger.info("Disassociated principal {} with portfolio {}".format(portfolio_principal_ARN,portfolio['portfolio_name']))
        for portfolio_principal_ARN in conf_portfolio_principals:
            if portfolio_principal_ARN not in sc_portfolio_principal_ARN_list:
                try:
                    if "arn:aws:iam:::role" in portfolio_principal_ARN:
                        servicecatalog_client.associate_principal_with_portfolio(PortfolioId=sc_portfolio_id,PrincipalType='IAM_PATTERN',PrincipalARN=portfolio_principal_ARN)
                    else:
                        servicecatalog_client.associate_principal_with_portfolio(PortfolioId=sc_portfolio_id,PrincipalType='IAM',PrincipalARN=portfolio_principal_ARN)
                    logger.info("Associated principal {} with portfolio {}".format(portfolio_principal_ARN,portfolio['portfolio_name']))
                except ClientError as e:
                    logger.info("Failed to associate principal {} with portfolio {}. Error message: {}".format(portfolio_principal_ARN,portfolio['portfolio_name'],e))
    else:
        for sc_portfolio_principal_ARN in sc_portfolio_principal_ARN_list:
            servicecatalog_client.disassociate_principal_from_portfolio(PortfolioId=sc_portfolio_id,PrincipalARN=sc_portfolio_principal_ARN)
            logger.info("Disassociated principal {} with portfolio {}".format(sc_portfolio_principal_ARN,portfolio['portfolio_name']))

exitcode=0
logger.info("Buildspec create portfolio share job exit code is {}".format(exitcode))
