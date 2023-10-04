# Driving Enterprise Service Catalog with DevOps mindset and tools
## Description

This solution that will streamline the validation, publishing, distribution, and consumption of infrastructure-as-code (IaC), secure-by-design blueprints. In addition to the capability to integrate automated controls and programmatic configurations such as cfn-nag, this solution simplifies the consumption of blueprint across AWS Organizations by removing dependency on legacy human-dependent processes which reduce the blueprint consumption lead time from days to seconds.

## Getting Started

### Two main components of this solution are:

* Blueprint factory
* Blueprint release pipeline

#### Blueprint factory overview

Figure 1 shows the Blueprint factory pipeline architecture.

![Blueprint factory](/images/figure1.png "Figure 1")

This is a config-driven architecture, and the config file is stored in Blueprint factory repo. This config file consists of Service Catalog portfolios and products. Once a Blueprint template is completed, the Blueprint developer will update the product details in the config file. This will trigger the Blueprint Factory pipeline. Below are the different stages of Blueprint factory pipeline.

Stage A – Deploy portfolios: If you are triggering the pipeline for the first time, it’ll start with deploy portfolio stage. At this stage the pipeline deploys two portfolios, one Internal portfolio and one Blueprint portfolio. The products that are managed by Blueprint Factory Admin team are associated with Internal portfolio. One example of an internal product is Blueprint release pipeline that is used to deploy the Blueprints as Service Catalog products. We will talk more about this pipeline as we unveil further steps in the architecture. 

Stage B – Share blueprint portfolio: In the next stage of Blueprint factory pipeline, the Blueprint portfolio is shared with AWS Organizations Units. This will enable consumer accounts that are OU members to seamlessly consume the Blueprint products that are associated with Blueprint portfolio.

Stage C – Deploy blueprint release pipeline product: In this stage, the pipeline will fetch Blueprint release pipeline stack from the Blueprint factory CodeCommit repo and deploys it as a Service Catalog product. This product is then associated with the Internal portfolio that is created in Stage A. This will help us with the version control of the Blueprint release pipeline stack. 

Stage D – Create blueprint release pipelines: In stage D, based on the product details updated in the config file, factory pipeline will prepare the stack parameters and will provision the Blueprint product release pipeline product from Service Catalog. This will launch Cloudformation stack of Blueprint product release pipeline which in turn will create the pipeline for the Blueprint product (we will discuss about the various stages of Blueprint product release pipeline in the next section). After successful execution of all stages, release pipeline will deploy the Blueprint as Service Catalog product and associate it with Blueprint Portfolio. Since this portfolio is shared with Organization Units, Blueprint product is now available for consumer accounts for consumption.

#### Blueprint release pipeline overview

Figure 2 shows the Blueprint release pipeline architecture.

![Blueprint release pipeline](/images/figure2.png "Figure 2")

A single blueprint development follows GitOps flow, that means its developed and uploaded to a feature branch and a pull request is submitted for review. Once its reviewed and approved after peer review and cyber review process, its merged into a master branch. This will then trigger Blueprint release pipeline. 
In the first stage, the template is checked for file alignment. The pipeline accepts both cloudformation and cdk formats. If the file is a cdk construct, it will be synthesised to a cloudformation template. Second stage will perform syntax check of the template using cfn-lint. In the next stage, pipeline performs various control checks with tools like cfn-nag and cloud conformity. The Cloud Conformity service is accessed via an API call, the credentials for which is stored in Secrets Manager. Once the reports are received from cloud conformity, it will be stored in a S3 bucket and it will be also shared to Blueprint Factory Admin team via SNS topic. In the next two stages, pipeline will deploy the new version of the product and it’ll also perform version control. When there is an update in the blueprint template and bump up in the version, pipeline version control stage will make older versions of the product inactive.

### Dependencies

* AWS accounts.
* AWS Organizations set up in case of  Multi-Accounts with Management-Tenant model.
* Access to CloudFormation  and permissions to deploy a CloudFormation stack with the ability to  create the following resources: AWS CodeCommit - CodeCommit repos, AWS CodePipeline  - Code pipelines, AWS CodeBuild - CodeBuild projects, AWS  Service Catalog - Portfolios, Products and Provisioned products, AWS  Systems Manager - Parameter store, Amazon CloudWatch Log Group  - CloudWatch Log group, Amazon S3 - S3 buckets, Amazon  EventBridge – EventBusPolicy and Rule, Amazon SNS – Topic,  TopicPolicy and Subscription, AWS KMS – Key and KeyPolicy, and AWS  IAM – Role and Policy.

### Installing

* Clone Gitlab repositories and create CodeCommit reportories
```
git clone git@github.com:aws-samples/aws-enterprise-blueprint-factory-blueprint-repo.git ServiceCatalog-BlueprintProductRepo
git clone git@github.com:aws-samples/aws-enterprise-blueprint-factory-config-repo.git ServiceCatalog-ConfigRepo
git clone git@github.com:aws-samples/aws-enterprise-blueprint-factory-code-repo.git ServiceCatalog-CodeRepo
aws codecommit create-repository --repository-name ServiceCatalog-ConfigRepo
aws codecommit create-repository --repository-name ServiceCatalog-CodeRepo
aws codecommit create-repository --repository-name ServiceCatalog-BlueprintProductRepo
cd ServiceCatalog-ConfigRepo
git remote rename origin origin-gitlab
git remote add origin https://git-codecommit.<region>.amazonaws.com/v1/repos/ServiceCatalog-ConfigRepo
git push origin main
cd ../ServiceCatalog-CodeRepo
git remote rename origin origin-gitlab
git remote add origin https://git-codecommit.<region>.amazonaws.com/v1/repos/ServiceCatalog-CodeRepo
git push origin main
cd ../ServiceCatalog-BlueprintProductRepo
git remote rename origin origin-gitlab
git remote add origin https://git-codecommit.<region>.amazonaws.com/v1/repos/ServiceCatalog-BlueprintProductRepo
git push origin main
```

### Cleanup

* Remove all the Service Catalog products
```
* Comment all products in bp_config.yml
* Commit and push to ServiceCatalog-ConfigRepo
```

* Remove all the Service Catalog portolios
```
* Comment all portfolios in bp_config.yml
* Commit and push to ServiceCatalog-ConfigRepo
```

## Authors

Joe Guo
[@guojing1217](https://github.com/guojing1217)

Shreejesh MV
[@shreejeshmv](https://github.com/shreejeshmv)

Haofei Feng
[@haofeif](https://github.com/haofeif)


## Version History

* 0.1
    * Initial Release

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.

## Acknowledgments

Inspiration, code snippets, etc.
* [cfn-lint](https://github.com/aws-cloudformation/cfn-lint)
* [cfn-nag](https://github.com/stelligent/cfn_nag)
