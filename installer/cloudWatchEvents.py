import boto3

ev_client = boto3.client('events')

def cloudwatch_create_daily_rule_for_function(lambda_function_name, lambda_function_arn, iam_arn):

    ruleName = "daily-event-for-{}".format(lambda_function_name)

    rule = ev_client.put_rule(
        Name=ruleName,
        ScheduleExpression='rate(1 day)',
        State='ENABLED',
        Description='Executed every day',
        RoleArn=iam_arn
    )

    ev_client.put_targets(
        Rule=ruleName,
        Targets=[
            {
                'Id': lambda_function_name,
                'Arn': lambda_function_arn
            }
        ]
    )

    return rule