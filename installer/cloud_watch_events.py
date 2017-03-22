import boto3

ev_client = boto3.client('events')


def cloudwatch_create_daily_rule_for_function(lambda_function_name, lambda_function_arn, iam_arn):

    rule_name = "daily-event-for-{}".format(lambda_function_name)

    rule = ev_client.put_rule(
        Name=rule_name,
        ScheduleExpression='rate(1 day)',
        State='ENABLED',
        Description='Executed every day',
        RoleArn=iam_arn
    )

    ev_client.put_targets(
        Rule=rule_name,
        Targets=[
            {
                'Id': lambda_function_name,
                'Arn': lambda_function_arn
            }
        ]
    )

    return rule