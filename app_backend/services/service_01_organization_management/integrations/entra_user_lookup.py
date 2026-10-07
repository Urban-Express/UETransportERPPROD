import os
import requests
import msal
from dotenv import load_dotenv

load_dotenv()

ENTRA_TENANT_ID = os.getenv("ENTRA_DIR_ID")
ENTRA_CLIENT_ID = os.getenv("ENTRA_APP_ID")
ENTRA_CLIENT_SECRET = os.getenv("ENTRA_SECRET")
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def get_graph_access_token():
    """
    Gets Microsoft Graph access token using client credentials flow.
    This is used by the backend to read users from Entra.
    """

    authority = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}"

    app = msal.ConfidentialClientApplication(
        client_id=ENTRA_CLIENT_ID,
        authority=authority,
        client_credential=ENTRA_CLIENT_SECRET
    )

    token_result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)

    if "access_token" not in token_result:
        raise Exception(f"Failed to get Graph token: {token_result}")

    return token_result["access_token"]


def get_entra_user_by_upn_or_email(user_identifier: str):
    """
    Pulls one user from Microsoft Entra using UPN or email.

    Example input:
        test@urbanexpress.ae

    Returns:
        dict containing normalized user fields for user_master.
    """

    if not user_identifier:
        raise Exception("user_identifier is required to search Entra user.")

    access_token = get_graph_access_token()

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    url = (
        f"{GRAPH_BASE_URL}/users/{user_identifier}"
        "?$select=id,userPrincipalName,mail,displayName,givenName,surname,"
        "mobilePhone,businessPhones,accountEnabled"
    )

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code == 404:
        raise Exception(f"User not found in Entra: {user_identifier}")

    if response.status_code != 200:
        raise Exception(
            f"Failed to fetch Entra user. "
            f"Status: {response.status_code}, Response: {response.text}"
        )

    user = response.json()

    business_phones = user.get("businessPhones") or []

    return {
        "auth_provider": "ENTRA",
        "entra_object_id": user.get("id"),
        "user_principal_name": user.get("userPrincipalName"),
        "password_hash": None,
        "first_name": user.get("givenName"),
        "last_name": user.get("surname"),
        "display_name": user.get("displayName"),
        "email": user.get("mail") or user.get("userPrincipalName"),
        "phone_number": user.get("mobilePhone") or (business_phones[0] if business_phones else None),
        "is_active": bool(user.get("accountEnabled", True)),
        "is_deleted": False
    }


def enrich_payload_from_entra_if_required(payload: dict):
    """
    This is the main helper function.

    If auth_provider = ENTRA:
        - pull user details from Entra
        - overwrite identity fields in payload with Entra values
        - keep org/department/created_by/updated_by from payload

    If auth_provider = LOCAL:
        - return payload as-is
    """

    auth_provider = payload.get("auth_provider") or "LOCAL"

    if auth_provider not in ("LOCAL", "ENTRA"):
        raise Exception("Invalid auth_provider. Expected LOCAL or ENTRA.")

    if auth_provider != "ENTRA":
        return payload

    user_identifier = (
        payload.get("user_principal_name")
        or payload.get("email")
    )

    if not user_identifier:
        raise Exception(
            "For ENTRA users, either user_principal_name or email is required."
        )

    entra_user_data = get_entra_user_by_upn_or_email(user_identifier)

    enriched_payload = payload.copy()

    # Entra-controlled identity fields
    enriched_payload["auth_provider"] = "ENTRA"
    enriched_payload["entra_object_id"] = entra_user_data.get("entra_object_id")
    enriched_payload["user_principal_name"] = entra_user_data.get("user_principal_name")
    enriched_payload["password_hash"] = None
    enriched_payload["first_name"] = entra_user_data.get("first_name")
    enriched_payload["last_name"] = entra_user_data.get("last_name")
    enriched_payload["display_name"] = entra_user_data.get("display_name")
    enriched_payload["email"] = entra_user_data.get("email")
    enriched_payload["phone_number"] = entra_user_data.get("phone_number")
    enriched_payload["is_active"] = entra_user_data.get("is_active")
    enriched_payload["is_deleted"] = entra_user_data.get("is_deleted")

    return enriched_payload