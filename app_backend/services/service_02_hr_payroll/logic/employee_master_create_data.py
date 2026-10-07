from app_backend.services.service_01_organization_management.data.db_connect_engine import db_engine
from sqlalchemy import text
import pandas as pd


# Create new employee
def create_employee_master(payload: dict):
    employee_engine = None
    try:
        employee_engine = db_engine()
        employee_id = payload.get("employee_id")
        empl_org_id_fk = payload.get("empl_org_id_fk") or payload.get("authenticated_org_id")

        if employee_id and empl_org_id_fk:
            get_employee_id = text("""
                select employee_id
                from employee_master
                where empl_org_id_fk = :empl_org_id_fk
                and lower(employee_id) = lower(:employee_id)
            """)

            with employee_engine.begin() as conn:
                df_employee_id = pd.read_sql(
                    sql=get_employee_id,
                    con=conn,
                    params={
                        "empl_org_id_fk": empl_org_id_fk,
                        "employee_id": employee_id
                    }
                )

            if not df_employee_id.empty:
                return {"error": f"Employee already exists: {employee_id}"}

        insert_into_employee_master = text("""
            insert into employee_master(
                empl_org_id_fk,
                secondary_key_dept,
                employee_id,
                employee_name,
                employee_designation,
                joining_date,
                nationality,
                passport_number,
                passport_expiry_date,
                passport_issuing_country,
                passport_with,
                visa_number,
                visa_expiry_date,
                emirates_id_number,
                emirates_id_expiry_date,
                phone_number_company,
                phone_number_personal,
                phone_number_home,
                email_id_company,
                email_id_personal,
                address_in_base_location,
                address_in_home_location,
                driver_licence_number,
                driver_licence_expiry_date,
                driver_licence_type,
                permit_number,
                permit_expiry_date,
                permit_type,
                insurance_number,
                insurance_expiry_date,
                bank_name,
                bank_address,
                bank_account_number,
                monthly_basic_salary,
                monthly_allowance,
                monthly_accomodation,
                field_flex_field_1,
                field_flex_field_2,
                field_flex_field_3,
                field_flex_field_4,
                created_by,
                updated_by,
                employee_image_path,
                reporting_to_employee_id
            ) values (
                :empl_org_id_fk,
                :secondary_key_dept,
                :employee_id,
                :employee_name,
                :employee_designation,
                :joining_date,
                :nationality,
                :passport_number,
                :passport_expiry_date,
                :passport_issuing_country,
                :passport_with,
                :visa_number,
                :visa_expiry_date,
                :emirates_id_number,
                :emirates_id_expiry_date,
                :phone_number_company,
                :phone_number_personal,
                :phone_number_home,
                :email_id_company,
                :email_id_personal,
                :address_in_base_location,
                :address_in_home_location,
                :driver_licence_number,
                :driver_licence_expiry_date,
                :driver_licence_type,
                :permit_number,
                :permit_expiry_date,
                :permit_type,
                :insurance_number,
                :insurance_expiry_date,
                :bank_name,
                :bank_address,
                :bank_account_number,
                :monthly_basic_salary,
                :monthly_allowance,
                :monthly_accomodation,
                :field_flex_field_1,
                :field_flex_field_2,
                :field_flex_field_3,
                :field_flex_field_4,
                :created_by,
                :updated_by,
                :employee_image_path,
                :reporting_to_employee_id
            )
        """)

        params_insert = {
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
            "created_by": payload.get("created_by"),
            "updated_by": payload.get("updated_by"),
            "employee_image_path": None,
            "reporting_to_employee_id": payload.get("reporting_to_employee_id")
        }

        with employee_engine.begin() as conn:
            conn.execute(insert_into_employee_master, params_insert)

        return {
            "message": f"Successfully created employee: {employee_id}",
            "employee_image_path": None,
        }

    except Exception as e:
        return {"error": f"Failed to create employee. Error Message: {str(e)}"}
    finally:
        if employee_engine is not None:
            employee_engine.dispose()


def create_employee(payload: dict):
    return create_employee_master(payload)
