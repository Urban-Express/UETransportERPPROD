from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Delete customer
def delete_customer_master(payload: dict):
    try:
        customer_engine = db_engine()
        cust_id = (
            payload.get("cust_id")
            or payload.get("cust_id_pk")
        )
        cust_org_id_fk = payload.get("cust_org_id_fk") or payload.get("authenticated_org_id")

        if not cust_id:
            return {"error": "cust_id is required."}

        get_cust_id = text("""
            select cust_id_pk
            from customer_master
            where cust_id_pk = :cust_id
            and cust_org_id_fk = :cust_org_id_fk
        """)

        with customer_engine.begin() as conn:
            df_cust_id = pd.read_sql(
                sql=get_cust_id,
                con=conn,
                params={
                    "cust_id": cust_id,
                    "cust_org_id_fk": cust_org_id_fk
                }
            )

        if df_cust_id.empty:
            return {"error": "Customer ID not found."}

        delete_customer_master_query = text("""
            delete from customer_master
            where cust_id_pk = :cust_id
            and cust_org_id_fk = :cust_org_id_fk
        """)

        params_delete = {
            "cust_id": cust_id,
            "cust_org_id_fk": cust_org_id_fk
        }

        with customer_engine.begin() as conn:
            conn.execute(delete_customer_master_query, params_delete)

        return {"message": f"Successfully deleted customer ID: {cust_id}"}

    except Exception as e:
        return {"error": f"Failed to delete customer. Error Message: {str(e)}"}


def delete_customer(payload: dict):
    return delete_customer_master(payload)
