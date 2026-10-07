from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from app_backend.services.service_01_organization_management.integrations.entra_user_lookup import enrich_payload_from_entra_if_required
from sqlalchemy import text
import pandas as pd
import bcrypt


# Update existing user
def update_user(payload: dict):
    try:
        user_engine = db_engine()

        user_id = payload.get("user_id")
        user_org_id_fk = payload.get("user_org_id_fk") or payload.get("authenticated_org_id")

        if not user_id:
            return {"error": "user_id is required."}

        get_user_id = text("""
            select user_id_pk
            from user_master
            where user_id_pk = :user_id
            and user_org_id_fk = :user_org_id_fk
        """)

        with user_engine.begin() as conn:
            df_user_id = pd.read_sql(
                sql=get_user_id,
                con=conn,
                params={
                    "user_id": user_id,
                    "user_org_id_fk": user_org_id_fk
                }
            )

        if df_user_id.empty:
            return {"error": "User ID not found."}

        auth_provider = payload.get("auth_provider") or "LOCAL"

        if auth_provider not in ("LOCAL", "ENTRA"):
            return {"error": "Invalid auth provider. Expected LOCAL or ENTRA."}

        # Pull from Entra only if auth_provider = ENTRA
        if auth_provider == "ENTRA":
            payload = enrich_payload_from_entra_if_required(payload)
            password_hash = None

        else:
            password = payload.get("password")

            if password:
                password_hash = bcrypt.hashpw(
                    password.encode("utf-8"),
                    bcrypt.gensalt()
                ).decode("utf-8")
            else:
                # Preserve existing password hash if no new password is supplied
                get_existing_password = text("""
                    select password_hash
                    from user_master
                    where user_id_pk = :user_id
                    and user_org_id_fk = :user_org_id_fk
                """)

                with user_engine.begin() as conn:
                    existing_password_result = conn.execute(
                        get_existing_password,
                        {
                            "user_id": user_id,
                            "user_org_id_fk": user_org_id_fk
                        }
                    ).fetchone()

                if existing_password_result is None:
                    return {"error": "User ID not found."}

                password_hash = existing_password_result.password_hash

        update_user_master = text("""
            update user_master
            set
                auth_provider = :auth_provider,
                entra_object_id = :entra_object_id,
                user_principal_name = :user_principal_name,
                password_hash = :password_hash,
                first_name = :first_name,
                last_name = :last_name,
                display_name = :display_name,
                email = :email,
                phone_number = :phone_number,
                user_org_id_fk = :user_org_id_fk,
                user_department_id_fk = :user_department_id_fk,
                is_active = :is_active,
                is_deleted = :is_deleted,
                updated_by = :updated_by,
                updated_at = current_timestamp
            where user_id_pk = :user_id
            and user_org_id_fk = :user_org_id_fk
        """)

        params_update = {
            "user_id": user_id,
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
            "updated_by": payload.get("updated_by")
        }

        with user_engine.begin() as conn:
            conn.execute(update_user_master, params_update)

        return {
            "message": f"Successfully updated user: {payload.get('email')}",
            "auth_provider": auth_provider,
            "entra_object_id": payload.get("entra_object_id")
        }

    except Exception as e:
        return {"error": f"Failed to update user. Error Message: {str(e)}"}
