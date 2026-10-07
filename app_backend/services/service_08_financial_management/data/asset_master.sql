BEGIN;

CREATE TABLE public.asset_master
(
    asset_id_pk                SERIAL,
    asset_org_id_fk            INTEGER NOT NULL,

    asset_location             TEXT,

    asset_type                 VARCHAR(100) NOT NULL,

    asset_name                 TEXT NOT NULL,

    asset_acquisition_date     DATE,
    depreciation_start_date    DATE,

    asset_acquisition_cost     NUMERIC,
    asset_useful_life          NUMERIC,
    asset_salvage_value        NUMERIC,
    asset_nbv                  NUMERIC,

    created_at                 TIMESTAMP WITHOUT TIME ZONE
                               NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by                 VARCHAR(100)
                               NOT NULL DEFAULT CURRENT_USER,

    updated_at                 TIMESTAMP WITHOUT TIME ZONE
                               NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by                 VARCHAR(100),

    CONSTRAINT pk_asset_master
        PRIMARY KEY (asset_id_pk),

    CONSTRAINT fk_asset_master_organization
        FOREIGN KEY (asset_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT ck_asset_master_type
        CHECK
        (
            asset_type IN
            (
                'Land and land improvements',
                'Buildings',
                'Plant, machinery and equipment',
                'Vehicles (non-revenue generating)',
                'Furniture and fixtures',
                'Computer software',
                'Computer hardware',
                'Other assets'
            )
        )
);


CREATE INDEX idx_asset_master_org
    ON public.asset_master (asset_org_id_fk);


CREATE INDEX idx_asset_master_type
    ON public.asset_master (asset_org_id_fk, asset_type);


CREATE INDEX idx_asset_master_name
    ON public.asset_master (asset_org_id_fk, asset_name);


/*
    Automatically update updated_at whenever
    an asset_master record is modified.
*/
CREATE OR REPLACE FUNCTION public.fn_asset_master_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


CREATE TRIGGER trg_asset_master_set_updated_at
BEFORE UPDATE
ON public.asset_master
FOR EACH ROW
EXECUTE FUNCTION public.fn_asset_master_set_updated_at();


COMMIT;