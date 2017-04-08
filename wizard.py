#!/usr/bin/env python
"""Lambda Lets-Encrypt Configuration/Setup Tool

This is a wizard that will help you configure the Lambda function to
automatically manage your SSL certifcates for CloudFront Distributions.

Usage:
  wizard.py
  wizard.py (-h | --help)
  wizard.py --version
  wizard.py --update-lambda

Options:
    -h --help         Show this screen
    --version         Show the version
    --update-lambda   Bundle zip from existing config and upload to lambda
"""
from __future__ import print_function
import os
import json
import time
import zipfile
from docopt import docopt
from string import Template
from installer import terminal, ec2, sns, cloudfront, iam, s3, awslambda, elb, route53, cloud_watch_events

acme_challenge_file_name = 'simple_acme.py'
lambda_file_name = 'lambda_function.py'
zip_file_name = 'lambda-letsencrypt-dist.zip'
config_file_template_name = 'config.py.dist'
generated_config_file_name = 'config-wizard.py'
config_file_name = 'config.py'


def choose_aws_region():
    region_names = ec2.list_region_names()
    options = []
    for i, name in enumerate(region_names):
        options.append({
            'selector': i,
            'prompt': name,
            'return': name
        })
    return terminal.get_selection("Select AWS region to use:", options, prompt_after="Which AWS region?",
                                  allow_empty=False)


def choose_s3_bucket():
    bucket_list = s3.s3_list_buckets()
    options = []
    for i, bucket in enumerate(bucket_list):
        options.append({
            'selector': i,
            'prompt': bucket,
            'return': bucket
        })
    return terminal.get_selection("Select the S3 Bucket to use:", options, prompt_after="Which S3 Bucket?",
                                  allow_empty=False)


def choose_lambda_function_for_update():
    function_names = awslambda.list_function_names()
    options = []
    for i, name in enumerate(function_names):
        options.append({
            'selector': i,
            'prompt': name,
            'return': name
        })
    return terminal.get_selection("Select function to update:", options, prompt_after="Which Lambda function?",
                                  allow_empty=False)


def wizard_elb(global_config):
    terminal.print_header("ELB Configuration")
    terminal.write_str("""\
        Now we'll detect your existing Elastic Load Balancers and allow you
        to configure them to use SSL. You must select the domain names
        you want on the certificate for each ELB.""")
    terminal.write_str("""\
        Note that only DNS validation(via Route53) is supported for ELBs""")
    print()

    global_config['elb_sites'] = []
    global_config['elb_domains'] = []

    # Get the list of all Cloudfront Distributions
    elb_list = elb.list_elbs()
    elb_list_opts = []
    for i, elb_name in enumerate(elb_list):
        elb_list_opts.append({
            'selector': i,
            'prompt': elb_name,
            'return': elb_name
        })

    route53_list = route53.list_zones()
    route53_list_opts = []
    for i, zone in enumerate(route53_list):
        route53_list_opts.append({
            'selector': i,
            'prompt': "{} - {}".format(zone['Name'], zone['Id']),
            'return': zone
        })

    while True:
        lb = terminal.get_selection("Choose an ELB to configure SSL for(Leave blank for none)", elb_list_opts,
                                    prompt_after="Which ELB?", allow_empty=True)
        if lb is None:
            break

        lb_port = terminal.get_input("What port number will this certificate be for(HTTPS is 443) [443]?",
                                     allow_empty=True)
        if len(lb_port) == 0:
            lb_port = 443

        domains = []
        while True:
            if len(domains) > 0:
                print("Already selected: {}".format(",".join(domains)))
            zone = terminal.get_selection("Choose a Route53 Zone that points to this load balancer: ",
                                          route53_list_opts, prompt_after="Which zone?", allow_empty=True)
            # stop when they don't enter anything
            if zone is None:
                break

            # Only allow adding each domain once
            if zone['Name'] in domains:
                continue
            domains.append(zone['Name'])
            global_config['elb_domains'].append({
                'DOMAIN': zone['Name'],
                'ROUTE53_ZONE_ID': zone['Id'],
                'VALIDATION_METHODS': ['dns-01']
            })

        site = {
            'ELB_NAME': lb,
            'ELB_PORT': lb_port,
            'DOMAINS': domains,
        }
        global_config['elb_sites'].append(site)


def wizard_cf(global_config):
    terminal.print_header("CloudFront Configuration")

    global_config['cf_sites'] = []
    global_config['cf_domains'] = []

    # Get the list of all Cloudfront Distributions
    cf_dist_list = cloudfront.list_distributions()
    cf_dist_opts = []
    for i, d in enumerate(cf_dist_list):
        cf_dist_opts.append({
            'selector': i,
            'prompt': "{} - {} ({}) ".format(d['Id'], d['Comment'], ", ".join(d['Aliases'])),
            'return': d
        })

    terminal.write_str("""\
        Now we'll detect your existing CloudFront Distributions and allow you
        to configure them to use SSL. Domain names will be automatically
        detected from the 'Aliases/CNAMEs' configuration section of each
        Distribution.""")
    print()
    terminal.write_str("""\
        You will configure each Distribution fully before being presented with
        the list of Distributions again. You can configure as many Distributions
        as you like.""")
    while True:
        print()
        dist = terminal.get_selection(
            "Select a CloudFront Distribution to configure with Lets-Encrypt(leave blank to finish)", cf_dist_opts,
            prompt_after="Which CloudFront Distribution?", allow_empty=True)
        if dist is None:
            break

        cnames = dist['Aliases']
        terminal.write_str("The following domain names exist for the selected CloudFront Distribution:")
        terminal.write_str("    " + ", ".join(cnames))
        terminal.write_str("""\
            Each domain in this list will be validated with Lets-Encrypt and added to the certificate assigned to this
            Distribution.""")
        print()
        for dns_name in cnames:
            domain = {
                'DOMAIN': dns_name,
                'VALIDATION_METHODS': []
            }
            print("Choose validation methods for the domain '{}'".format(dns_name))
            route53_id = route53.get_zone_id(dns_name)
            if route53_id:
                terminal.write_str(terminal.Colors.OKGREEN + "Route53 zone detected!" + terminal.Colors.ENDC)
                validate_via_dns = terminal.get_yn("Validate using DNS", default=False)
                if validate_via_dns:
                    domain['ROUTE53_ZONE_ID'] = route53_id
                    domain['VALIDATION_METHODS'].append('dns-01')
            else:
                terminal.write_str(
                    terminal.Colors.WARNING +
                    "No Route53 zone detected, DNS validation not possible." +
                    terminal.Colors.ENDC)

            validate_via_http = terminal.get_yn("Validate using HTTP", default=True)
            if validate_via_http:
                domain['CLOUDFRONT_ID'] = dist['Id']
                domain['VALIDATION_METHODS'].append('http-01')

            global_config['cf_domains'].append(domain)
        site = {
            'CLOUDFRONT_ID': dist['Id'],
            'DOMAINS': cnames
        }
        global_config['cf_sites'].append(site)


def wizard_namespace(global_config):
    namespace = None

    terminal.print_header("Namespace")
    terminal.write_str("""\
        It is necessary to provide unique names when creating configuration and
        challenge S3 buckets; provided value will be appended to default names.
        In other cases uniqueness is not necessary although helpful if you need to
        distinguish among resources.""")

    namespace = terminal.get_input("Enter value to append to resource names (eg: foobar): ", allow_empty=False)
    global_config['namespace'] = namespace


def wizard_region(global_config):
    terminal.print_header("AWS Region")
    terminal.write_str("""Choose the region you want ot use for new resources""")

    aws_region = choose_aws_region()

    global_config['aws_region'] = aws_region


def wizard_sns(global_config):
    sns_email = None

    terminal.print_header("Notifications")
    terminal.write_str("""\
        The lambda function can send notifications when a certificate is issued,
        errors occur, or other things that may need your attention.
        Notifications are optional.""")

    use_sns = True
    sns_email = terminal.get_input("Enter the email address for notifications(blank to disable): ", allow_empty=True)
    if len(sns_email) == 0:
        use_sns = False

    global_config['use_sns'] = use_sns
    global_config['sns_email'] = sns_email


def wizard_s3_cfg_bucket(global_config):
    terminal.print_header("S3 Configuration Bucket")
    terminal.write_str("""\
        An S3 Bucket is required to store configuration. If you already have a bucket you want to use for this choose no
        and select it from the list. Otherwise let the wizard create one for you.""")
    create_s3_cfg_bucket = terminal.get_yn("Create a bucket for configuration", True)

    if create_s3_cfg_bucket:
        s3_cfg_bucket = "lambda-letsencrypt-config-{}".format(global_config['namespace'])
    else:
        s3_cfg_bucket = choose_s3_bucket()

    global_config['create_s3_cfg_bucket'] = create_s3_cfg_bucket
    global_config['s3_cfg_bucket'] = s3_cfg_bucket


def wizard_iam(global_config):
    terminal.print_header("IAM Configuration")
    terminal.write_str("""\
        An IAM role must be created for this lambda function giving it access to CloudFront, Route53, S3,
        SNS(notifications), IAM(certificates), and CloudWatch(logs/alarms).""")
    print()
    terminal.write_str(
        "If you do not let the wizard create this role you will be asked to select an existing role to use.")
    create_iam_role = terminal.get_yn("Do you want to automatically create this role", True)
    if not create_iam_role:
        role_list = iam.list_roles()
        options = []
        for i, role in enumerate(role_list):
            options.append({
                'selector': i,
                'prompt': role,
                'return': role
            })
        iam_role_name = terminal.get_selection("Select the IAM Role:", options, prompt_after="Which IAM Role?",
                                               allow_empty=False)
    else:
        iam_role_name = "lambda-letsencrypt-{}".format(global_config['namespace'])

    global_config['create_iam_role'] = create_iam_role
    global_config['iam_role_name'] = iam_role_name


def wizard_challenges(global_config):
    create_s3_challenge_bucket = False
    s3_challenge_bucket = None

    terminal.print_header("Lets-Encrypt Challenge Validation Settings")
    terminal.write_str("""\
        This tool will handle validation of your domains automatically. There are two possible validation methods: HTTP
        and DNS.""")
    print()
    terminal.write_str("""\
        HTTP validation is only available for CloudFront sites. It requires an S3 bucket to store the challenge
        responses in. This bucket needs to be publicly accessible. Your CloudFront Distribution(s) will be reconfigured
        to use this bucket as an origin for challenge responses.""")
    terminal.write_str("If you do not configure a bucket for this you will only be able to use DNS validation.")
    print()
    terminal.write_str("""\
        DNS validation requires your domain to be managed with Route53. This validation method is always available and
        requires no additional configuration.""")
    terminal.write_str(
        terminal.Colors.WARNING +
        "Note: DNS validation is currently only supported by the staging server." +
        terminal.Colors.ENDC)
    print()
    terminal.write_str("Each domain you want to manage can be configured to validate using either of these methods.")
    print()

    use_http_challenges = terminal.get_yn("Do you want to configure HTTP validation", True)
    if use_http_challenges:
        create_s3_challenge_bucket = terminal.get_yn(
            "Do you want to create a bucket for these challenges(Choose No to select an existing bucket)", True)
        if create_s3_challenge_bucket:
            s3_challenge_bucket = "lambda-letsencrypt-challenges-{}".format(global_config['namespace'])
        else:
            s3_challenge_bucket = choose_s3_bucket()
    else:
        # only dns challenge support is available
        pass

    global_config['use_http_challenges'] = use_http_challenges
    global_config['create_s3_challenge_bucket'] = create_s3_challenge_bucket
    global_config['s3_challenge_bucket'] = s3_challenge_bucket


def wizard_trigger(global_config):
    terminal.print_header("Lets-Encrypt certificate check trigger")
    terminal.write_str("""\
        To set up certificate and later update it, it is necessary to invoke
         generated AWS Lambda function regularly. Lambda function will make sure
         certificate is issued (might take 2-3 invocations), will check its expiry,
         will update it before it expires.""")
    print()
    terminal.write_str("""\
        Trigger is created as a AWS CloudWatch Event rule with target
        pointing to Lambda function.""")
    print()
    terminal.write_str("""\
        If you skip set up of this trigger, you will need to either invoke
        function yourself or set up trigger which does it.""")
    print()
    create_cloudwatch_rule = terminal.get_yn("Set up AWS Lambda trigger?", default=True)
    global_config['create_cloudwatch_rule'] = create_cloudwatch_rule


def wizard_summary(global_config):
    gc = global_config

    terminal.print_header("**Summary**")
    print("Namespace:                                       {}".format(gc['namespace']))
    print("AWS region:                                      {}".format(gc['namespace']))
    print("Notification Email:                              {}".format(gc['sns_email'] or "(notifications disabled)"))
    print("S3 Config Bucket:                                {}".format(gc['s3_cfg_bucket']), end="")
    if gc['create_s3_cfg_bucket']:
        print(" (to be created)")
    else:
        print(" (existing)")

    if gc['create_iam_role']:
        print("IAM Role Name:                                   {} (to be created)".format(gc['iam_role_name']))
    else:
        print("IAM Role Name:                                   {} (existing)".format(gc['iam_role_name']))

    print("Support HTTP Challenges:                         {}".format(gc['use_http_challenges']))
    if gc['use_http_challenges']:
        print("S3 HTTP Challenge Bucket:                        {}".format(gc['s3_challenge_bucket']), end="")
        if gc['create_s3_challenge_bucket']:
            print(" (to be created)")
        else:
            print(" (existing)")

    print("Domains To Manage With Lets-Encrypt")
    for d in gc['cf_domains']:
        print("    {} - [{}]".format(d['DOMAIN'], ",".join(d['VALIDATION_METHODS'])))
    for d in gc['elb_domains']:
        print("    {} - [{}]".format(d['DOMAIN'], ",".join(d['VALIDATION_METHODS'])))

    print("CloudFront Distributions To Manage:")
    for cf in gc['cf_sites']:
        print("    {} - [{}]".format(cf['CLOUDFRONT_ID'], ",".join(cf['DOMAINS'])))

    print("Elastic Load Balancers to Manage:")
    for lb in gc['elb_sites']:
        print("    {}:{} - [{}]".format(lb['ELB_NAME'], lb['ELB_PORT'], ",".join(lb['DOMAINS'])))

    print("Create daily Lambda function trigger:            {}".format(gc['create_cloudwatch_rule']))


def wizard_save_config(global_config):
    terminal.print_header("Making Requested Changes")
    templatevars = {}
    with open(config_file_template_name, 'r') as template:
        configfile = Template(template.read())

    templatevars['SNS_ARN'] = None
    templatevars['NOTIFY_EMAIL'] = None
    templatevars['AWS_REGION'] = global_config['aws_region']

    # Configure SNS if appropriate
    sns_arn = None
    if len(global_config['sns_email']) > 0:
        # Create SNS Topic if necessary
        print("Creating SNS Topic for Notifications ", end='')
        sns_arn = sns.get_or_create_topic(global_config['sns_email'])
        if sns_arn is False or sns_arn is None:
            print(terminal.Colors.FAIL + u'\u2717' + terminal.Colors.ENDC)
        else:
            print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)
            templatevars['SNS_ARN'] = sns_arn
            templatevars['NOTIFY_EMAIL'] = global_config['sns_email']

    # create config bucket if necessary
    if global_config['create_s3_cfg_bucket']:
        print("Creating S3 Configuration Bucket ", end='')
        s3.create_bucket(global_config['aws_region'], global_config['s3_cfg_bucket'])
        print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)

    # create challenge bucket if necessary(needs to be configured as static website)
    if global_config['create_s3_challenge_bucket']:
        print("Creating S3 Challenge Bucket ", end='')
        s3.create_web_bucket(global_config['aws_region'], global_config['s3_challenge_bucket'])
        print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)

    # create IAM role if required
    if global_config['create_iam_role']:
        policy_document = iam.generate_policy_document(
            s3buckets=[
                global_config['s3_cfg_bucket'],
                global_config['s3_challenge_bucket']
            ],
            snstopicarn=sns_arn
        )
        iam_arn = iam.configure(global_config['iam_role_name'], policy_document)
        # attempt to avoid error: The role defined for the function cannot be assumed by Lambda.
        print("Wait for IAM role")
        time.sleep(5)
        print("Still waiting..")
        time.sleep(5)
        print("Still waiting...")
        time.sleep(5)

    templatevars['S3_CONFIG_BUCKET'] = global_config['s3_cfg_bucket']
    templatevars['S3_CHALLENGE_BUCKET'] = global_config['s3_challenge_bucket']

    domains = global_config['cf_domains'] + global_config['elb_domains']
    sites = global_config['cf_sites'] + global_config['elb_sites']
    templatevars['DOMAINS'] = json.dumps(domains, indent=4)
    templatevars['SITES'] = json.dumps(sites, indent=4)

    # write out the config file
    config = configfile.substitute(templatevars)
    with open(generated_config_file_name, 'w') as configfinal:
        print("Writing Configuration File ", end='')
        configfinal.write(config)
        print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)

    if not create_lambda_zip():
        return

    print("Configuring Lambda Function:")
    iam_arn = iam.get_arn(global_config['iam_role_name'])
    print("    IAM ARN: {}".format(iam_arn))
    print("    Uploading Function ", end='')
    lambda_function_name = "lambda-letsencrypt-{}".format(global_config['namespace'])
    lambda_function = awslambda.create_function(lambda_function_name, iam_arn, zip_file_name)
    if lambda_function:
        print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)
    else:
        print(terminal.Colors.FAIL + u'\u2717' + terminal.Colors.ENDC)
        return

    if global_config['create_cloudwatch_rule']:
        print("    Setting daily Lambda function trigger ", end='')
        lambda_execution_rule = cloud_watch_events.cloudwatch_create_daily_rule_for_function(
            lambda_function['FunctionName'], lambda_function['FunctionArn'], iam_arn)
        if lambda_execution_rule:
            print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)
        else:
            print(terminal.Colors.FAIL + u'\u2717' + terminal.Colors.ENDC)
            return

    terminal.print_header("Testing")
    terminal.write_str("""\
        You may want to test this before you set it to be recurring. Click on
        the 'Test' button in the AWS Console for the lambda-letsencrypt function.
        The data you provide to this function does not matter. Make sure to review
        the logs after it finishes and check for anything out of the ordinary.
    """)
    print()
    terminal.write_str("""\
        It will take at least 2 runs before your certificates are issued,
        maybe 3 depending on how fast cloudfront responds. This is because it
        needs one try to configure cloudfront, one to submit the challenge and
        have it verified, and one final run to issue the certificate and configure
        the cloudfront distribution
    """)


def create_lambda_zip():

    print("Creating Zip File To Upload To Lambda")

    try:
        os.remove(zip_file_name)
    except OSError:
        pass

    if not os.path.isfile(generated_config_file_name):
        print(terminal.Colors.FAIL +
              "Missing {} file which is created using {} template".format(generated_config_file_name, config_file_template_name) +
              terminal.Colors.ENDC)
        return False

    archive_success = True
    archive = zipfile.ZipFile(zip_file_name, mode='w')
    try:
        for f in [lambda_file_name, acme_challenge_file_name]:
            print("    Adding '{}'".format(f))
            archive.write(f)
        print("    Adding '{}'".format(config_file_name))
        archive.write(generated_config_file_name, config_file_name)
    except Exception as e:
        print(terminal.Colors.FAIL + 'Zip File Creation Failed' + terminal.Colors.ENDC)
        print(e)
        archive_success = False
    finally:
        print('Zip File Created Successfully')
        archive.close()

    if not archive_success:
        print("Could not create lambda zip file")

    return archive_success


def update_lambda():
    terminal.print_header("Updating Lambda function")
    if not create_lambda_zip():
        return
    fn = choose_lambda_function_for_update()
    updated = awslambda.update_function_code(fn, zip_file_name)
    if updated:
        print(terminal.Colors.OKGREEN + u'\u2713' + terminal.Colors.ENDC)
    else:
        print(terminal.Colors.FAIL + u'\u2717' + terminal.Colors.ENDC)
        return


def wizard():
    terminal.print_header("Lambda Lets-Encrypt Wizard")
    terminal.write_str("""\
        This wizard will guide you through the process of setting up your existing
        CloudFront Distributions to use SSL certificates provided by Lets-Encrypt
        and automatically issued/maintained by an AWS Lambda function.

        These certificates are free of charge, and valid for 90 days. This wizard
        will also set up a Lambda function that is responsible for issuing and
        renewing these certificates automatically as they near their expiration
        date.

        The cost of the AWS services used to make this work are typically less
        than a penny per month. For full pricing details please refer to the
        docs.
    """)

    print()
    print(terminal.Colors.WARNING + "WARNING: ")
    terminal.write_str("""\
        Manual configuration is required at this time to configure the Lambda
        function to run on a daily basis to keep your certificate updated. If
        you do not follow the steps provided at the end of this wizard your
        Lambda function will *NOT* run.
    """)
    print(terminal.Colors.ENDC)

    global_config = {}

    wizard_namespace(global_config)
    wizard_region(global_config)
    wizard_sns(global_config)
    wizard_iam(global_config)
    wizard_s3_cfg_bucket(global_config)
    wizard_challenges(global_config)
    wizard_cf(global_config)
    wizard_elb(global_config)
    wizard_trigger(global_config)

    cfg_menu = [
        {'selector': 0, 'prompt': 'Namespace', 'return': wizard_namespace},
        {'selector': 1, 'prompt': 'AWS Region', 'return': wizard_region},
        {'selector': 2, 'prompt': 'SNS', 'return': wizard_sns},
        {'selector': 3, 'prompt': 'IAM', 'return': wizard_iam},
        {'selector': 4, 'prompt': 'S3 Config', 'return': wizard_s3_cfg_bucket},
        {'selector': 5, 'prompt': 'Challenges', 'return': wizard_challenges},
        {'selector': 6, 'prompt': 'CloudFront', 'return': wizard_cf},
        {'selector': 7, 'prompt': 'Elastic Load Balancers', 'return': wizard_cf},
        {'selector': 8, 'prompt': 'Lambda function trigger', 'return': wizard_trigger},
        {'selector': 9, 'prompt': 'Done', 'return': None}
    ]

    finished = False
    while not finished:
        wizard_summary(global_config)
        finished = terminal.get_yn("Are these settings correct", True)
        if not finished:
            selection = terminal.get_selection("Which section do you want to change", cfg_menu,
                                               prompt_after="Which section to modify?", allow_empty=False)
            if selection:
                selection(global_config)

    wizard_save_config(global_config)


if __name__ == "__main__":
    args = docopt(__doc__, version='Lambda Lets-Encrypt 1.0')
    if args['--update-lambda']:
        update_lambda()
    else:
        wizard()


