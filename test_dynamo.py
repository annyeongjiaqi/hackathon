import boto3
from dotenv import load_dotenv
load_dotenv()

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
table = dynamodb.Table("user_preferences")

table.put_item(Item={"session_id": "test123", "test_field": "hello"})
response = table.get_item(Key={"session_id": "test123"})
print(response["Item"])