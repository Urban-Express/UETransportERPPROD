from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def delete_supplier_master(payload: dict):
    supplier_engine = None
    try:
        supplier_engine = db_engine()
        supp_id = (
            payload.get("supp_id")
            or payload.get("supp_id_pk")
        )
        supp_org_id_fk = payload.get("supp_org_id_fk") or payload.get("authenticated_org_id")

        if not supp_id:
            return {"error": "supp_id is required."}

        get_supp_id = text("""
            select supp_id_pk
            from supplier_master
            where supp_id_pk = :supp_id
            and supp_org_id_fk = :supp_org_id_fk
        """)

        with supplier_engine.begin() as conn:
            df_supp_id = pd.read_sql(
                sql=get_supp_id,
                con=conn,
                params={
                    "supp_id": supp_id,
                    "supp_org_id_fk": supp_org_id_fk
                }
            )

        if df_supp_id.empty:
            return {"error": "Supplier ID not found."}

        delete_supplier_master_query = text("""
            delete from supplier_master
            where supp_id_pk = :supp_id
            and supp_org_id_fk = :supp_org_id_fk
        """)

        with supplier_engine.begin() as conn:
            conn.execute(
                delete_supplier_master_query,
                {
                    "supp_id": supp_id,
                    "supp_org_id_fk": supp_org_id_fk
                }
            )

        return {"message": f"Successfully deleted supplier ID: {supp_id}"}

    except IntegrityError:
        return {
            "error": (
                "Supplier cannot be deleted because it is referenced by another record."
            )
        }
    except Exception as e:
        return {"error": f"Failed to delete supplier. Error Message: {str(e)}"}
    finally:
        if supplier_engine is not None:
            supplier_engine.dispose()


def delete_supplier(payload: dict):
    return delete_supplier_master(payload)
