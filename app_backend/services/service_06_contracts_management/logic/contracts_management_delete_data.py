from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


def delete_contracts_management(payload: dict):
    contract_engine = None
    try:
        contract_engine = db_engine()
        cont_id = (
            payload.get("cont_id")
            or payload.get("cont_id_pk")
        )
        cont_org_id_fk = payload.get("cont_org_id_fk") or payload.get("authenticated_org_id")

        if not cont_id:
            return {"error": "cont_id is required."}

        get_cont_id = text("""
            select cont_id_pk
            from contracts_management
            where cont_id_pk = :cont_id
            and cont_org_id_fk = :cont_org_id_fk
        """)

        with contract_engine.begin() as conn:
            df_cont_id = pd.read_sql(
                sql=get_cont_id,
                con=conn,
                params={
                    "cont_id": cont_id,
                    "cont_org_id_fk": cont_org_id_fk
                }
            )

        if df_cont_id.empty:
            return {"error": "Contract ID not found."}

        delete_contracts_management_query = text("""
            delete from contracts_management
            where cont_id_pk = :cont_id
            and cont_org_id_fk = :cont_org_id_fk
        """)

        with contract_engine.begin() as conn:
            conn.execute(
                delete_contracts_management_query,
                {
                    "cont_id": cont_id,
                    "cont_org_id_fk": cont_org_id_fk
                }
            )

        return {"message": f"Successfully deleted contract ID: {cont_id}"}

    except Exception as e:
        return {"error": f"Failed to delete contract. Error Message: {str(e)}"}
    finally:
        if contract_engine is not None:
            contract_engine.dispose()


def delete_contract(payload: dict):
    return delete_contracts_management(payload)
