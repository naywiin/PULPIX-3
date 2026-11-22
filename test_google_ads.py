from google.ads.googleads.client import GoogleAdsClient

config = {
    "use_application_default_credentials": True,
    "use_proto_plus": True,
}

client = GoogleAdsClient.load_from_dict(config)

customer_service = client.get_service("CustomerService")
response = customer_service.list_accessible_customers()

print("\nCUENTAS GOOGLE ADS ACCESIBLES:")
for resource_name in response.resource_names:
    print(resource_name)
