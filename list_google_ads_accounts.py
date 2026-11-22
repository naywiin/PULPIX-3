from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

config = {
    "use_application_default_credentials": True,
    "use_proto_plus": True,
}

client = GoogleAdsClient.load_from_dict(config)

customer_service = client.get_service("CustomerService")
googleads_service = client.get_service("GoogleAdsService")

accessible = customer_service.list_accessible_customers()

print("\nCUENTAS GOOGLE ADS:\n")

for resource_name in accessible.resource_names:
    customer_id = resource_name.split("/")[-1]

    query = """
        SELECT
          customer.id,
          customer.descriptive_name,
          customer.manager,
          customer.currency_code,
          customer.time_zone
        FROM customer
        LIMIT 1
    """

    try:
        response = googleads_service.search(
            customer_id=customer_id,
            query=query,
        )

        for row in response:
            tipo = "MCC / MANAGER" if row.customer.manager else "CUENTA CLIENTE"

            print(f"ID: {row.customer.id}")
            print(f"Nombre: {row.customer.descriptive_name}")
            print(f"Tipo: {tipo}")
            print(f"Moneda: {row.customer.currency_code}")
            print(f"Zona horaria: {row.customer.time_zone}")
            print("-" * 50)

    except GoogleAdsException as ex:
        print(f"ID: {customer_id}")
        print("No se pudo consultar:")
        for error in ex.failure.errors:
            print(error.message)
        print("-" * 50)
