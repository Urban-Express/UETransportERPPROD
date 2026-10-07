from sqlalchemy import text
import pandas as pd

from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine


ASSET_MASTER_FIELDS = [
    "asset_org_id_fk",
    "asset_location",
    "asset_type",
    "asset_name",
    "asset_acquisition_date",
    "depreciation_start_date",
    "asset_acquisition_cost",
    "asset_useful_life",
    "asset_salvage_value",
    "asset_nbv",
    "created_by",
    "updated_by",
]

ASSET_BUSINESS_FIELDS = [
    "asset_org_id_fk",
    "asset_location",
    "asset_type",
    "asset_name",
    "asset_acquisition_date",
    "depreciation_start_date",
    "asset_acquisition_cost",
    "asset_useful_life",
    "asset_salvage_value",
    "asset_nbv",
]

ASSET_TYPES = {
    "Land and land improvements",
    "Buildings",
    "Plant, machinery and equipment",
    "Vehicles (non-revenue generating)",
    "Furniture and fixtures",
    "Computer software",
    "Computer hardware",
    "Other assets",
}


def get_asset_id_from_payload(payload: dict):
    return payload.get("asset_id") or payload.get("asset_id_pk")


def get_asset_params(payload: dict):
    return {
        "asset_org_id_fk": payload.get("asset_org_id_fk") or payload.get("authenticated_org_id"),
        "asset_location": payload.get("asset_location"),
        "asset_type": payload.get("asset_type"),
        "asset_name": payload.get("asset_name"),
        "asset_acquisition_date": payload.get("asset_acquisition_date"),
        "depreciation_start_date": payload.get("depreciation_start_date"),
        "asset_acquisition_cost": payload.get("asset_acquisition_cost"),
        "asset_useful_life": payload.get("asset_useful_life"),
        "asset_salvage_value": payload.get("asset_salvage_value"),
        "asset_nbv": payload.get("asset_nbv"),
        "created_by": payload.get("created_by"),
        "updated_by": payload.get("updated_by"),
    }


def validate_asset_payload(payload: dict, require_id: bool = False):
    if require_id and not get_asset_id_from_payload(payload):
        return {"error": "asset_id is required."}

    if payload.get("asset_org_id_fk") is None:
        return {"error": "asset_org_id_fk is required."}

    if not payload.get("asset_type"):
        return {"error": "asset_type is required."}

    if payload.get("asset_type") not in ASSET_TYPES:
        return {"error": f"Invalid asset_type: {payload.get('asset_type')}"}

    if not payload.get("asset_name"):
        return {"error": "asset_name is required."}

    return None


def get_asset_by_id(asset_id, asset_org_id_fk=None, conn=None):
    get_asset_query = text("""
        select *
        from asset_master
        where asset_id_pk = :asset_id
        and (:asset_org_id_fk is null or asset_org_id_fk = :asset_org_id_fk)
    """)
    params = {
        "asset_id": asset_id,
        "asset_org_id_fk": asset_org_id_fk
    }
    if conn is not None:
        df_asset = pd.read_sql(sql=get_asset_query, con=conn, params=params)
    else:
        asset_engine = db_engine()
        with asset_engine.begin() as asset_conn:
            df_asset = pd.read_sql(sql=get_asset_query, con=asset_conn, params=params)

    if df_asset.empty:
        return None

    return df_asset.iloc[0].to_dict()


def verify_organization_exists(asset_org_id_fk, conn=None):
    get_org_query = text("""
        select org_id_pk
        from organization_master
        where org_id_pk = :asset_org_id_fk
    """)
    if conn is not None:
        df_org = pd.read_sql(
            sql=get_org_query,
            con=conn,
            params={"asset_org_id_fk": asset_org_id_fk}
        )
    else:
        asset_engine = db_engine()
        with asset_engine.begin() as asset_conn:
            df_org = pd.read_sql(
                sql=get_org_query,
                con=asset_conn,
                params={"asset_org_id_fk": asset_org_id_fk}
            )

    return not df_org.empty
