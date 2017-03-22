import boto3

ec2_client = boto3.client('ec2')


def list_region_names():

    regions = ec2_client.describe_regions()
    region_names = []
    for region in regions['Regions']:
        region_names.append(region['RegionName'])
    return region_names
