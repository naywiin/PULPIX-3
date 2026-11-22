from google.analytics.admin_v1beta import AnalyticsAdminServiceClient

client = AnalyticsAdminServiceClient()

print("\nCUENTAS Y PROPIEDADES GOOGLE ANALYTICS:\n")

response = client.list_account_summaries()

for account in response:
    print(f"Cuenta: {account.display_name}")
    print(f"Account resource: {account.account}")

    for prop in account.property_summaries:
        print(f"  - Propiedad: {prop.display_name}")
        print(f"    Property resource: {prop.property}")

    print("-" * 60)
