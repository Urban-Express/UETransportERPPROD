from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Update existing employee
def update_employee_master(payload: dict):
    employee_engine = None
    try:
        employee_engine = db_engine()
        empl_id = (
            payload.get("empl_id")
            or payload.get("empl_id_pk")
        )
        empl_org_id_fk = payload.get("empl_org_id_fk") or payload.get("authenticated_org_id")

        if not empl_id:
            return {"error": "empl_id is required."}

        get_empl_id = text("""
            select empl_id_pk, employee_image_path
            from employee_master
            where empl_id_pk = :empl_id
            and empl_org_id_fk = :empl_org_id_fk
        """)

        with employee_engine.begin() as conn:
            df_empl_id = pd.read_sql(
                sql=get_empl_id,
                con=conn,
                params={
                    "empl_id": empl_id,
                    "empl_org_id_fk": empl_org_id_fk
                }
            )

        if df_empl_id.empty:
            return {"error": "Employee ID not found."}

        update_employee_master_query = text("""
            update employee_master
            set
                empl_org_id_fk = :empl_org_id_fk,
                secondary_key_dept = :secondary_key_dept,
                employee_id = :employee_id,
                employee_name = :employee_name,
                employee_designation = :employee_designation,
                joining_date = :joining_date,
                nationality = :nationality,
                passport_number = :passport_number,
                passport_expiry_date = :passport_expiry_date,
                passport_issuing_country = :passport_issuing_country,
                passport_with = :passport_with,
                visa_number = :visa_number,
                visa_expiry_date = :visa_expiry_date,
                emirates_id_number = :emirates_id_number,
                emirates_id_expiry_date = :emirates_id_expiry_date,
                phone_number_company = :phone_number_company,
                phone_number_personal = :phone_number_personal,
                phone_number_home = :phone_number_home,
                email_id_company = :email_id_company,
                email_id_personal = :email_id_personal,
                address_in_base_location = :address_in_base_location,
                address_in_home_location = :address_in_home_location,
                driver_licence_number = :driver_licence_number,
                driver_licence_expiry_date = :driver_licence_expiry_date,
                driver_licence_type = :driver_licence_type,
                permit_number = :permit_number,
                permit_expiry_date = :permit_expiry_date,
                permit_type = :permit_type,
                insurance_number = :insurance_number,
                insurance_expiry_date = :insurance_expiry_date,
                bank_name = :bank_name,
                bank_address = :bank_address,
                bank_account_number = :bank_account_number,
                monthly_basic_salary = :monthly_basic_salary,
                monthly_allowance = :monthly_allowance,
                monthly_accomodation = :monthly_accomodation,
                field_flex_field_1 = :field_flex_field_1,
                field_flex_field_2 = :field_flex_field_2,
                field_flex_field_3 = :field_flex_field_3,
                field_flex_field_4 = :field_flex_field_4,
                updated_by = :updated_by,
                reporting_to_employee_id = :reporting_to_employee_id
            where empl_id_pk = :empl_id
            and empl_org_id_fk = :empl_org_id_fk
            returning empl_id_pk, employee_image_path
        """)

        params_update = {
            "empl_id": empl_id,
            "empl_org_id_fk": empl_org_id_fk,
            "secondary_key_dept": payload.get("secondary_key_dept"),
            "employee_id": payload.get("employee_id"),
            "employee_name": payload.get("employee_name"),
            "employee_designation": payload.get("employee_designation"),
            "joining_date": payload.get("joining_date"),
            "nationality": payload.get("nationality"),
            "passport_number": payload.get("passport_number"),
            "passport_expiry_date": payload.get("passport_expiry_date"),
            "passport_issuing_country": payload.get("passport_issuing_country"),
            "passport_with": payload.get("passport_with"),
            "visa_number": payload.get("visa_number"),
            "visa_expiry_date": payload.get("visa_expiry_date"),
            "emirates_id_number": payload.get("emirates_id_number"),
            "emirates_id_expiry_date": payload.get("emirates_id_expiry_date"),
            "phone_number_company": payload.get("phone_number_company"),
            "phone_number_personal": payload.get("phone_number_personal"),
            "phone_number_home": payload.get("phone_number_home"),
            "email_id_company": payload.get("email_id_company"),
            "email_id_personal": payload.get("email_id_personal"),
            "address_in_base_location": payload.get("address_in_base_location"),
            "address_in_home_location": payload.get("address_in_home_location"),
            "driver_licence_number": payload.get("driver_licence_number"),
            "driver_licence_expiry_date": payload.get("driver_licence_expiry_date"),
            "driver_licence_type": payload.get("driver_licence_type"),
            "permit_number": payload.get("permit_number"),
            "permit_expiry_date": payload.get("permit_expiry_date"),
            "permit_type": payload.get("permit_type"),
            "insurance_number": payload.get("insurance_number"),
            "insurance_expiry_date": payload.get("insurance_expiry_date"),
            "bank_name": payload.get("bank_name"),
            "bank_address": payload.get("bank_address"),
            "bank_account_number": payload.get("bank_account_number"),
            "monthly_basic_salary": payload.get("monthly_basic_salary", 0),
            "monthly_allowance": payload.get("monthly_allowance", 0),
            "monthly_accomodation": payload.get("monthly_accomodation", 0),
            "field_flex_field_1": payload.get("field_flex_field_1"),
            "field_flex_field_2": payload.get("field_flex_field_2"),
            "field_flex_field_3": payload.get("field_flex_field_3"),
            "field_flex_field_4": payload.get("field_flex_field_4"),
            "updated_by": payload.get("updated_by"),
            "reporting_to_employee_id": payload.get("reporting_to_employee_id")
        }

        with employee_engine.begin() as conn:
            updated_employee = conn.execute(
                update_employee_master_query,
                params_update,
            ).mappings().one_or_none()

        if not updated_employee:
            return {"error": "Employee ID not found at execution time."}

        return {
            "message": f"Successfully updated employee: {payload.get('employee_id')}",
            "employee_image_path": updated_employee["employee_image_path"],
        }

    except Exception as e:
        return {"error": f"Failed to update employee. Error Message: {str(e)}"}
    finally:
        if employee_engine is not None:
            employee_engine.dispose()


def update_employee(payload: dict):
    return update_employee_master(payload)
