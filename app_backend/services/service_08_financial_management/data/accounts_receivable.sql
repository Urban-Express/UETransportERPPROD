BEGIN;

CREATE TABLE public.accounts_receivables
(
    ar_id_pk                 SERIAL,

    ar_org_id_fk             INTEGER NOT NULL,
    ar_cust_id_fk            INTEGER NOT NULL,

    ar_invoice_number        VARCHAR(100) NOT NULL,
    ar_invoice_date          DATE NOT NULL,
    ar_due_date              DATE,

    ar_contract              TEXT,

    ar_description           TEXT,

    ar_currency_code         VARCHAR(3) NOT NULL,

    ar_invoice_amount        NUMERIC(18,2) NOT NULL,
    ar_tax_amount            NUMERIC(18,2) NOT NULL DEFAULT 0,
    ar_received_amount       NUMERIC(18,2) NOT NULL DEFAULT 0,

    ar_balance_amount        NUMERIC(18,2)
        GENERATED ALWAYS AS
        (ar_invoice_amount - ar_received_amount) STORED,

    ar_approval_status       VARCHAR(30) NOT NULL DEFAULT 'DRAFT',

    ar_collection_status     VARCHAR(30) NOT NULL DEFAULT 'OUTSTANDING',

    ar_approval_comments     TEXT,

    ar_approved_by           VARCHAR(100),
    ar_approved_at           TIMESTAMP WITHOUT TIME ZONE,

    ar_notes                 TEXT,

    created_at               TIMESTAMP WITHOUT TIME ZONE
                             NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by               VARCHAR(100)
                             NOT NULL DEFAULT CURRENT_USER,

    updated_at               TIMESTAMP WITHOUT TIME ZONE
                             NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by               VARCHAR(100),

    CONSTRAINT pk_accounts_receivables
        PRIMARY KEY (ar_id_pk),

    CONSTRAINT fk_ar_organization
        FOREIGN KEY (ar_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_ar_customer
        FOREIGN KEY
        (
            ar_cust_id_fk,
            ar_org_id_fk
        )
        REFERENCES public.customer_master
        (
            cust_id_pk,
            cust_org_id_fk
        )
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT uq_ar_invoice_number
        UNIQUE
        (
            ar_org_id_fk,
            ar_invoice_number
        ),

    CONSTRAINT ck_ar_approval_status
        CHECK
        (
            ar_approval_status IN
            (
                'DRAFT',
                'PENDING_APPROVAL',
                'APPROVED',
                'REJECTED',
                'CANCELLED'
            )
        ),

    CONSTRAINT ck_ar_collection_status
        CHECK
        (
            ar_collection_status IN
            (
                'OUTSTANDING',
                'PARTIALLY_RECEIVED',
                'RECEIVED'
            )
        ),

    CONSTRAINT ck_ar_invoice_amount
        CHECK (ar_invoice_amount >= 0),

    CONSTRAINT ck_ar_tax_amount
        CHECK (ar_tax_amount >= 0),

    CONSTRAINT ck_ar_received_amount
        CHECK (ar_received_amount >= 0),

    CONSTRAINT ck_ar_due_date
        CHECK
        (
            ar_due_date IS NULL
            OR ar_due_date >= ar_invoice_date
        )
);


CREATE INDEX idx_ar_org
    ON public.accounts_receivables (ar_org_id_fk);


CREATE INDEX idx_ar_customer
    ON public.accounts_receivables
    (
        ar_org_id_fk,
        ar_cust_id_fk
    );


CREATE INDEX idx_ar_invoice_date
    ON public.accounts_receivables
    (
        ar_org_id_fk,
        ar_invoice_date
    );


CREATE INDEX idx_ar_due_date
    ON public.accounts_receivables
    (
        ar_org_id_fk,
        ar_due_date
    );


CREATE INDEX idx_ar_approval_status
    ON public.accounts_receivables
    (
        ar_org_id_fk,
        ar_approval_status
    );


CREATE INDEX idx_ar_collection_status
    ON public.accounts_receivables
    (
        ar_org_id_fk,
        ar_collection_status
    );

CREATE OR REPLACE FUNCTION public.fn_accounts_receivables_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


CREATE TRIGGER trg_accounts_receivables_set_updated_at
BEFORE UPDATE
ON public.accounts_receivables
FOR EACH ROW
EXECUTE FUNCTION public.fn_accounts_receivables_set_updated_at();


COMMIT;

alter table accounts_receivables
add column ar_invoice_file_path TEXT;