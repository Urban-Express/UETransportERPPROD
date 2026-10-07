from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_01_organization_management.integrations.entra_user_lookup import enrich_payload_from_entra_if_required
from sqlalchemy import text
import pandas as pd
import bcrypt


# Create new user
def create_user(payload: dict):
    try:
        user_engine = db_engine()
        user_org_id_fk = payload.get("user_org_id_fk") or payload.get("authenticated_org_id")

        auth_provider = payload.get("auth_provider") or "LOCAL"

        if auth_provider not in ("LOCAL", "ENTRA"):
            return {"error": "Invalid auth provider. Expected LOCAL or ENTRA."}

        # Pull from Entra only if auth_provider = ENTRA
        if auth_provider == "ENTRA":
            payload = enrich_payload_from_entra_if_required(payload)
            password_hash = None

        else:
            password = payload.get("password")

            if not password:
                return {"error": "Password is required for LOCAL authentication."}

            password_hash = bcrypt.hashpw(
                password.encode("utf-8"),
                bcrypt.gensalt()
            ).decode("utf-8")

        # Check duplicate email
        get_user_email = text("""
            select email
            from user_master
            where lower(email) = lower(:email)
            and user_org_id_fk = :user_org_id_fk
        """)

        with user_engine.begin() as conn:
            df_user_email = pd.read_sql(
                sql=get_user_email,
                con=conn,
                params={
                    "email": payload.get("email"),
                    "user_org_id_fk": user_org_id_fk
                }
            )

        if not df_user_email.empty:
            return {"error": f"User already exists: {payload.get('email')}"}

        insert_into_user_master = text("""
            insert into user_master(
                auth_provider,
                entra_object_id,
                user_principal_name,
                password_hash,
                first_name,
                last_name,
                display_name,
                email,
                phone_number,
                user_org_id_fk,
                user_department_id_fk,
                is_active,
                is_deleted,
                created_by,
                updated_by
            ) values (
                :auth_provider,
                :entra_object_id,
                :user_principal_name,
                :password_hash,
                :first_name,
                :last_name,
                :display_name,
                :email,
                :phone_number,
                :user_org_id_fk,
                :user_department_id_fk,
                :is_active,
                :is_deleted,
                :created_by,
                :updated_by
            )
        """)

        params_insert = {
            "auth_provider": auth_provider,
            "entra_object_id": payload.get("entra_object_id"),
            "user_principal_name": payload.get("user_principal_name"),
            "password_hash": password_hash,
            "first_name": payload.get("first_name"),
            "last_name": payload.get("last_name"),
            "display_name": payload.get("display_name"),
            "email": payload.get("email"),
            "phone_number": payload.get("phone_number"),
            "user_org_id_fk": user_org_id_fk,
            "user_department_id_fk": payload.get("user_department_id_fk"),
            "is_active": payload.get("is_active", True),
            "is_deleted": payload.get("is_deleted", False),
            "created_by": payload.get("created_by"),
            "updated_by": payload.get("updated_by") or payload.get("created_by")
        }

        with user_engine.begin() as conn:
            conn.execute(insert_into_user_master, params_insert)

        return {
            "message": f"Successfully created user: {payload.get('email')}",
            "auth_provider": auth_provider,
            "entra_object_id": payload.get("entra_object_id")
        }

    except Exception as e:
        return {"error": f"Failed to create user. Error Message: {str(e)}"}
