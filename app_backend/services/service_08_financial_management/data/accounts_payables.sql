BEGIN;

CREATE TABLE public.accounts_payables
(
    ap_id_pk                 SERIAL,

    ap_org_id_fk             INTEGER NOT NULL,
    ap_supp_id_fk            INTEGER NOT NULL,

    ap_invoice_number        VARCHAR(100) NOT NULL,
    ap_invoice_date          DATE NOT NULL,
    ap_due_date              DATE,

    ap_description           TEXT,

    ap_currency_code         VARCHAR(3) NOT NULL,

    ap_invoice_amount        NUMERIC(18,2) NOT NULL,
    ap_tax_amount            NUMERIC(18,2) NOT NULL DEFAULT 0,
    ap_paid_amount           NUMERIC(18,2) NOT NULL DEFAULT 0,

    ap_balance_amount        NUMERIC(18,2)
        GENERATED ALWAYS AS
        (ap_invoice_amount - ap_paid_amount) STORED,

    ap_approval_status       VARCHAR(30) NOT NULL DEFAULT 'DRAFT',

    ap_payment_status        VARCHAR(30) NOT NULL DEFAULT 'UNPAID',

    ap_approval_comments     TEXT,

    ap_approved_by           VARCHAR(100),
    ap_approved_at           TIMESTAMP WITHOUT TIME ZONE,

    ap_notes                 TEXT,
    ap_invoice_file_path     TEXT,

    created_at               TIMESTAMP WITHOUT TIME ZONE
                             NOT NULL DEFAULT CURRENT_TIMESTAMP,

    created_by               VARCHAR(100)
                             NOT NULL DEFAULT CURRENT_USER,

    updated_at               TIMESTAMP WITHOUT TIME ZONE
                             NOT NULL DEFAULT CURRENT_TIMESTAMP,

    updated_by               VARCHAR(100),

    CONSTRAINT pk_accounts_payables
        PRIMARY KEY (ap_id_pk),

    CONSTRAINT fk_ap_organization
        FOREIGN KEY (ap_org_id_fk)
        REFERENCES public.organization_master (org_id_pk)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    /*
        Composite FK ensures that the selected supplier
        belongs to the same organization as the AP invoice.
    */
    CONSTRAINT fk_ap_supplier
        FOREIGN KEY
        (
            ap_supp_id_fk,
            ap_org_id_fk
        )
        REFERENCES public.supplier_master
        (
            supp_id_pk,
            supp_org_id_fk
        )
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    /*
        Prevent the same supplier invoice from being
        entered twice within the same organization.
    */
    CONSTRAINT uq_ap_supplier_invoice
        UNIQUE
        (
            ap_org_id_fk,
            ap_supp_id_fk,
            ap_invoice_number
        ),

    CONSTRAINT ck_ap_approval_status
        CHECK
        (
            ap_approval_status IN
            (
                'DRAFT',
                'PENDING_APPROVAL',
                'APPROVED',
                'REJECTED',
                'CANCELLED'
            )
        ),

    CONSTRAINT ck_ap_payment_status
        CHECK
        (
            ap_payment_status IN
            (
                'UNPAID',
                'PARTIALLY_PAID',
                'PAID'
            )
        ),

    CONSTRAINT ck_ap_invoice_amount
        CHECK (ap_invoice_amount >= 0),

    CONSTRAINT ck_ap_tax_amount
        CHECK (ap_tax_amount >= 0),

    CONSTRAINT ck_ap_paid_amount
        CHECK (ap_paid_amount >= 0),

    CONSTRAINT ck_ap_due_date
        CHECK
        (
            ap_due_date IS NULL
            OR ap_due_date >= ap_invoice_date
        )
);


CREATE INDEX idx_ap_org
    ON public.accounts_payables (ap_org_id_fk);


CREATE INDEX idx_ap_supplier
    ON public.accounts_payables
    (
        ap_org_id_fk,
        ap_supp_id_fk
    );


CREATE INDEX idx_ap_invoice_date
    ON public.accounts_payables
    (
        ap_org_id_fk,
        ap_invoice_date
    );


CREATE INDEX idx_ap_due_date
    ON public.accounts_payables
    (
        ap_org_id_fk,
        ap_due_date
    );


CREATE INDEX idx_ap_approval_status
    ON public.accounts_payables
    (
        ap_org_id_fk,
        ap_approval_status
    );


CREATE INDEX idx_ap_payment_status
    ON public.accounts_payables
    (
        ap_org_id_fk,
        ap_payment_status
    );


CREATE OR REPLACE FUNCTION public.fn_accounts_payables_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


CREATE TRIGGER trg_accounts_payables_set_updated_at
BEFORE UPDATE
ON public.accounts_payables
FOR EACH ROW
EXECUTE FUNCTION public.fn_accounts_payables_set_updated_at();


COMMIT;
