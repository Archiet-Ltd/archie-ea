"""flask seed-entity-schemas / flask backfill-vendor-element-ids.

The canonical vendor ArchiMate template records (SAP, Microsoft, Salesforce)
are now loaded by ``flask reference-packs load`` from
``app/seed_data/reference_packs/`` — see ``app/modules/reference_packs/``.
This module keeps the two commands that are not part of that fold: the
domain entity field seeds below (DataObject specs with ``spec_data_seed``)
and the ArchiMate element ID backfill.
"""
import click
from flask.cli import with_appcontext

from app import db


# ─── Domain entity field seeds ───────────────────────────────────────────────
# These are DataObject-type entries with spec_data_seed populated.
# They represent the actual data entities that appear in solutions built on
# these vendor platforms, not the infrastructure elements above.
#
# Field format: {"name": str, "type": str, "required": bool, "description": str}
# Types: string | integer | decimal | float | boolean | datetime | date | uuid | text | json
#
# Field names follow the canonical ERP field naming where applicable (SAP ABAP field
# name in parentheses in description) so architects know the source of truth.

_SAP_DATA_ENTITIES = [
    {
        "element_name": "Purchase Order",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 20,
        "spec_data_seed": {
            "fields": [
                {"name": "po_number",       "type": "string",   "required": True,  "description": "Document number (EBELN)"},
                {"name": "company_code",    "type": "string",   "required": True,  "description": "Organisational unit (BUKRS)"},
                {"name": "vendor_id",       "type": "string",   "required": True,  "description": "Vendor account number (LIFNR)"},
                {"name": "plant",           "type": "string",   "required": False, "description": "Receiving plant (WERKS)"},
                {"name": "purchasing_org",  "type": "string",   "required": True,  "description": "Purchasing organisation (EKORG)"},
                {"name": "purchasing_group","type": "string",   "required": False, "description": "Buyer group (EKGRP)"},
                {"name": "net_price",       "type": "decimal",  "required": True,  "description": "Net order price (NETPR)"},
                {"name": "currency",        "type": "string",   "required": True,  "description": "Currency key (WAERS)"},
                {"name": "payment_terms",   "type": "string",   "required": False, "description": "Payment terms key (ZTERM)"},
                {"name": "delivery_date",   "type": "date",     "required": False, "description": "Delivery date (EINDT)"},
                {"name": "gr_based_iv",     "type": "boolean",  "required": False, "description": "GR-based invoice verification (WEBRE)"},
                {"name": "status",          "type": "string",   "required": True,  "description": "Document status: open|closed|cancelled"},
                {"name": "created_at",      "type": "datetime", "required": True,  "description": "Creation timestamp (AEDAT)"},
                {"name": "created_by",      "type": "string",   "required": True,  "description": "Created by (ERNAM)"},
            ],
            "business_rules": [
                {"rule": "po_number must be unique per company_code", "type": "uniqueness"},
                {"rule": "net_price must be > 0", "type": "validation"},
                {"rule": "3-way match required: PO + GR + Invoice before payment release", "type": "process"},
                {"rule": "purchasing_org must belong to company_code", "type": "referential"},
            ],
        },
    },
    {
        "element_name": "Purchase Requisition",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 21,
        "spec_data_seed": {
            "fields": [
                {"name": "requisition_number", "type": "string",   "required": True,  "description": "Document number (BANFN)"},
                {"name": "item_number",        "type": "integer",  "required": True,  "description": "Item number (BNFPO)"},
                {"name": "material",           "type": "string",   "required": False, "description": "Material number (MATNR)"},
                {"name": "short_text",         "type": "string",   "required": True,  "description": "Short description (TXZ01)"},
                {"name": "quantity",           "type": "decimal",  "required": True,  "description": "Quantity (MENGE)"},
                {"name": "unit",               "type": "string",   "required": True,  "description": "Unit of measure (MEINS)"},
                {"name": "delivery_date",      "type": "date",     "required": True,  "description": "Required delivery date (LFDAT)"},
                {"name": "plant",              "type": "string",   "required": True,  "description": "Plant (WERKS)"},
                {"name": "cost_centre",        "type": "string",   "required": False, "description": "Cost centre (KOSTL)"},
                {"name": "requestor",          "type": "string",   "required": True,  "description": "Requestor (AFNAM)"},
                {"name": "release_status",     "type": "string",   "required": True,  "description": "Release strategy status: blocked|partial|released"},
                {"name": "created_at",         "type": "datetime", "required": True,  "description": "Creation timestamp"},
            ],
            "business_rules": [
                {"rule": "release_status must be 'released' before conversion to PO", "type": "process"},
                {"rule": "quantity must be > 0", "type": "validation"},
            ],
        },
    },
    {
        "element_name": "Vendor Invoice",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 22,
        "spec_data_seed": {
            "fields": [
                {"name": "invoice_number",    "type": "string",   "required": True,  "description": "Invoice document number (BELNR)"},
                {"name": "company_code",      "type": "string",   "required": True,  "description": "Company code (BUKRS)"},
                {"name": "fiscal_year",       "type": "integer",  "required": True,  "description": "Fiscal year (GJAHR)"},
                {"name": "vendor_id",         "type": "string",   "required": True,  "description": "Vendor account (LIFNR)"},
                {"name": "posting_date",      "type": "date",     "required": True,  "description": "Posting date (BUDAT)"},
                {"name": "document_date",     "type": "date",     "required": True,  "description": "Document date on invoice (BLDAT)"},
                {"name": "gross_amount",      "type": "decimal",  "required": True,  "description": "Gross invoice amount (WRBTR)"},
                {"name": "tax_amount",        "type": "decimal",  "required": False, "description": "Tax amount (WMWST)"},
                {"name": "currency",          "type": "string",   "required": True,  "description": "Currency (WAERS)"},
                {"name": "payment_terms",     "type": "string",   "required": False, "description": "Payment terms (ZTERM)"},
                {"name": "po_number",         "type": "string",   "required": False, "description": "Reference PO (EBELN)"},
                {"name": "reference",         "type": "string",   "required": False, "description": "Reference document number (XBLNR)"},
                {"name": "status",            "type": "string",   "required": True,  "description": "Status: parked|posted|cleared|blocked"},
                {"name": "withholding_tax",   "type": "boolean",  "required": False, "description": "Subject to withholding tax (QSSKZ)"},
            ],
            "business_rules": [
                {"rule": "3-way match: invoice must reference a posted GR before payment", "type": "process"},
                {"rule": "tolerance check: invoice amount must be within PO tolerance (default ±5%)", "type": "validation"},
                {"rule": "duplicate invoice check on (vendor_id, gross_amount, document_date)", "type": "uniqueness"},
            ],
        },
    },
    {
        "element_name": "Goods Receipt",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 23,
        "spec_data_seed": {
            "fields": [
                {"name": "gr_number",         "type": "string",   "required": True,  "description": "Material document number (MBLNR)"},
                {"name": "po_number",         "type": "string",   "required": True,  "description": "Reference PO (EBELN)"},
                {"name": "po_item",           "type": "integer",  "required": True,  "description": "PO line item (EBELP)"},
                {"name": "material",          "type": "string",   "required": False, "description": "Material number (MATNR)"},
                {"name": "plant",             "type": "string",   "required": True,  "description": "Receiving plant (WERKS)"},
                {"name": "storage_location",  "type": "string",   "required": False, "description": "Storage location (LGORT)"},
                {"name": "quantity_received", "type": "decimal",  "required": True,  "description": "Received quantity (MENGE)"},
                {"name": "unit",              "type": "string",   "required": True,  "description": "Unit of measure (MEINS)"},
                {"name": "movement_type",     "type": "string",   "required": True,  "description": "Movement type, e.g. 101=GR for PO (BWART)"},
                {"name": "posting_date",      "type": "date",     "required": True,  "description": "Posting date (BUDAT)"},
                {"name": "created_by",        "type": "string",   "required": True,  "description": "Created by (USNAM)"},
            ],
            "business_rules": [
                {"rule": "quantity_received must not exceed PO open quantity without tolerance override", "type": "validation"},
                {"rule": "movement_type 101 sets PO history flag for 3-way match", "type": "process"},
            ],
        },
    },
    {
        "element_name": "Vendor Master",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 24,
        "spec_data_seed": {
            "fields": [
                {"name": "vendor_id",         "type": "string",   "required": True,  "description": "Vendor account number (LIFNR)"},
                {"name": "name",              "type": "string",   "required": True,  "description": "Vendor name (NAME1)"},
                {"name": "country",           "type": "string",   "required": True,  "description": "Country key (LAND1)"},
                {"name": "currency",          "type": "string",   "required": True,  "description": "Invoice currency (WAERS)"},
                {"name": "payment_terms",     "type": "string",   "required": False, "description": "Payment terms (ZTERM)"},
                {"name": "payment_method",    "type": "string",   "required": False, "description": "Payment method (ZWELS)"},
                {"name": "bank_account",      "type": "string",   "required": False, "description": "Bank account number (BANKN)"},
                {"name": "tax_number",        "type": "string",   "required": False, "description": "Tax number 1 (STCD1)"},
                {"name": "account_group",     "type": "string",   "required": True,  "description": "Vendor account group (KTOKK)"},
                {"name": "blocked",           "type": "boolean",  "required": True,  "description": "Posting block (SPERR)"},
                {"name": "created_at",        "type": "datetime", "required": True,  "description": "Record creation date (ERDAT)"},
            ],
            "business_rules": [
                {"rule": "blocked vendors cannot be selected on new POs", "type": "process"},
                {"rule": "bank_account required before payment run", "type": "validation"},
            ],
        },
    },
    {
        "element_name": "Cost Centre",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 25,
        "spec_data_seed": {
            "fields": [
                {"name": "cost_centre",       "type": "string",   "required": True,  "description": "Cost centre number (KOSTL)"},
                {"name": "controlling_area",  "type": "string",   "required": True,  "description": "Controlling area (KOKRS)"},
                {"name": "name",              "type": "string",   "required": True,  "description": "Cost centre name (KTEXT)"},
                {"name": "valid_from",        "type": "date",     "required": True,  "description": "Valid from date (DATAB)"},
                {"name": "valid_to",          "type": "date",     "required": True,  "description": "Valid to date (DATBI)"},
                {"name": "manager_id",        "type": "string",   "required": False, "description": "Person responsible (VERAK)"},
                {"name": "cost_centre_type",  "type": "string",   "required": True,  "description": "Category (KOSAR): E=admin, F=production, H=auxiliary"},
                {"name": "currency",          "type": "string",   "required": True,  "description": "Cost centre currency (WAERS)"},
                {"name": "locked",            "type": "boolean",  "required": True,  "description": "Lock indicator for actual postings (SPEAA)"},
            ],
            "business_rules": [
                {"rule": "postings blocked to locked cost centres", "type": "process"},
                {"rule": "valid_from must be <= valid_to", "type": "validation"},
            ],
        },
    },
]

_MICROSOFT_DYNAMICS_DATA_ENTITIES = [
    {
        "element_name": "Account",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 20,
        "spec_data_seed": {
            "fields": [
                {"name": "account_id",        "type": "uuid",     "required": True,  "description": "Primary key (accountid)"},
                {"name": "name",              "type": "string",   "required": True,  "description": "Account name (name)"},
                {"name": "account_number",    "type": "string",   "required": False, "description": "Account number (accountnumber)"},
                {"name": "account_type",      "type": "string",   "required": False, "description": "Customer/Prospect/Partner (customertypecode)"},
                {"name": "industry",          "type": "string",   "required": False, "description": "Industry classification (industrycode)"},
                {"name": "annual_revenue",    "type": "decimal",  "required": False, "description": "Annual revenue (revenue)"},
                {"name": "number_of_employees","type":"integer",  "required": False, "description": "Number of employees (numberofemployees)"},
                {"name": "website",           "type": "string",   "required": False, "description": "Website URL (websiteurl)"},
                {"name": "phone",             "type": "string",   "required": False, "description": "Main phone (telephone1)"},
                {"name": "address_city",      "type": "string",   "required": False, "description": "City (address1_city)"},
                {"name": "address_country",   "type": "string",   "required": False, "description": "Country (address1_country)"},
                {"name": "owner_id",          "type": "uuid",     "required": True,  "description": "Record owner (ownerid)"},
                {"name": "status",            "type": "string",   "required": True,  "description": "Active/Inactive (statecode)"},
                {"name": "created_at",        "type": "datetime", "required": True,  "description": "Created on (createdon)"},
                {"name": "modified_at",       "type": "datetime", "required": True,  "description": "Modified on (modifiedon)"},
            ],
            "business_rules": [
                {"rule": "name is mandatory and must be unique within the organisation", "type": "uniqueness"},
                {"rule": "inactive accounts cannot be referenced on new opportunities", "type": "process"},
            ],
        },
    },
    {
        "element_name": "Opportunity",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 21,
        "spec_data_seed": {
            "fields": [
                {"name": "opportunity_id",    "type": "uuid",     "required": True,  "description": "Primary key (opportunityid)"},
                {"name": "name",              "type": "string",   "required": True,  "description": "Opportunity name (name)"},
                {"name": "account_id",        "type": "uuid",     "required": True,  "description": "Parent account (parentaccountid)"},
                {"name": "estimated_value",   "type": "decimal",  "required": False, "description": "Estimated revenue (estimatedvalue)"},
                {"name": "currency",          "type": "string",   "required": True,  "description": "Transaction currency (transactioncurrencyid)"},
                {"name": "stage",             "type": "string",   "required": True,  "description": "Pipeline stage (stagename): Qualify/Develop/Propose/Close"},
                {"name": "probability",       "type": "integer",  "required": False, "description": "Close probability % (closeprobability)"},
                {"name": "close_date",        "type": "date",     "required": True,  "description": "Estimated close date (estimatedclosedate)"},
                {"name": "owner_id",          "type": "uuid",     "required": True,  "description": "Record owner (ownerid)"},
                {"name": "lead_source",       "type": "string",   "required": False, "description": "Lead source (leadsourcecode)"},
                {"name": "status",            "type": "string",   "required": True,  "description": "Open/Won/Lost (statecode)"},
                {"name": "created_at",        "type": "datetime", "required": True,  "description": "Created on (createdon)"},
            ],
            "business_rules": [
                {"rule": "probability must update when stage changes (stage-probability mapping)", "type": "process"},
                {"rule": "close_date required before opportunity can progress past Propose stage", "type": "validation"},
                {"rule": "Won/Lost requires reason code (statusreason)", "type": "validation"},
            ],
        },
    },
    {
        "element_name": "Contact",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 22,
        "spec_data_seed": {
            "fields": [
                {"name": "contact_id",        "type": "uuid",     "required": True,  "description": "Primary key (contactid)"},
                {"name": "first_name",        "type": "string",   "required": True,  "description": "First name (firstname)"},
                {"name": "last_name",         "type": "string",   "required": True,  "description": "Last name (lastname)"},
                {"name": "email",             "type": "string",   "required": False, "description": "Primary email (emailaddress1)"},
                {"name": "phone",             "type": "string",   "required": False, "description": "Business phone (telephone1)"},
                {"name": "account_id",        "type": "uuid",     "required": False, "description": "Parent account (parentcustomerid)"},
                {"name": "job_title",         "type": "string",   "required": False, "description": "Job title (jobtitle)"},
                {"name": "department",        "type": "string",   "required": False, "description": "Department (department)"},
                {"name": "do_not_email",      "type": "boolean",  "required": True,  "description": "Email opt-out flag (donotemail)"},
                {"name": "owner_id",          "type": "uuid",     "required": True,  "description": "Record owner (ownerid)"},
                {"name": "status",            "type": "string",   "required": True,  "description": "Active/Inactive (statecode)"},
                {"name": "created_at",        "type": "datetime", "required": True,  "description": "Created on (createdon)"},
            ],
            "business_rules": [
                {"rule": "do_not_email=True must suppress all outbound marketing emails", "type": "process"},
            ],
        },
    },
]

# Standard Salesforce CRM objects — field API names in parentheses (REST/SOQL)
_SALESFORCE_DATA_ENTITIES = [
    {
        "element_name": "Account",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 20,
        "spec_data_seed": {
            "fields": [
                {"name": "id", "type": "string", "required": True, "description": "18-character record Id (Id)"},
                {"name": "name", "type": "string", "required": True, "description": "Account name (Name)"},
                {"name": "account_type", "type": "string", "required": False, "description": "Customer / Partner / Competitor (Type)"},
                {"name": "industry", "type": "string", "required": False, "description": "Industry picklist (Industry)"},
                {"name": "annual_revenue", "type": "decimal", "required": False, "description": "Annual revenue (AnnualRevenue)"},
                {"name": "number_of_employees", "type": "integer", "required": False, "description": "Employees (NumberOfEmployees)"},
                {"name": "billing_city", "type": "string", "required": False, "description": "Billing city (BillingCity)"},
                {"name": "billing_country", "type": "string", "required": False, "description": "Billing country (BillingCountry)"},
                {"name": "phone", "type": "string", "required": False, "description": "Main phone (Phone)"},
                {"name": "website", "type": "string", "required": False, "description": "Website URL (Website)"},
                {"name": "owner_id", "type": "string", "required": True, "description": "Record owner Id (OwnerId)"},
                {"name": "created_at", "type": "datetime", "required": True, "description": "Created timestamp (CreatedDate)"},
            ],
            "business_rules": [
                {"rule": "Name is required for B2B accounts in most validation rules", "type": "validation"},
            ],
        },
    },
    {
        "element_name": "Contact",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 21,
        "spec_data_seed": {
            "fields": [
                {"name": "id", "type": "string", "required": True, "description": "18-character record Id (Id)"},
                {"name": "first_name", "type": "string", "required": False, "description": "First name (FirstName)"},
                {"name": "last_name", "type": "string", "required": True, "description": "Last name (LastName)"},
                {"name": "email", "type": "string", "required": False, "description": "Email (Email)"},
                {"name": "phone", "type": "string", "required": False, "description": "Phone (Phone)"},
                {"name": "account_id", "type": "string", "required": False, "description": "Parent Account Id (AccountId)"},
                {"name": "title", "type": "string", "required": False, "description": "Title (Title)"},
                {"name": "department", "type": "string", "required": False, "description": "Department (Department)"},
                {"name": "owner_id", "type": "string", "required": True, "description": "Owner Id (OwnerId)"},
                {"name": "created_at", "type": "datetime", "required": True, "description": "Created timestamp (CreatedDate)"},
            ],
            "business_rules": [
                {"rule": "Duplicate rules often block duplicate Email per org", "type": "uniqueness"},
            ],
        },
    },
    {
        "element_name": "Lead",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 22,
        "spec_data_seed": {
            "fields": [
                {"name": "id", "type": "string", "required": True, "description": "18-character record Id (Id)"},
                {"name": "first_name", "type": "string", "required": False, "description": "First name (FirstName)"},
                {"name": "last_name", "type": "string", "required": False, "description": "Last name (LastName)"},
                {"name": "company", "type": "string", "required": True, "description": "Company (Company)"},
                {"name": "email", "type": "string", "required": False, "description": "Email (Email)"},
                {"name": "status", "type": "string", "required": True, "description": "Lead status (Status)"},
                {"name": "lead_source", "type": "string", "required": False, "description": "Lead source (LeadSource)"},
                {"name": "owner_id", "type": "string", "required": True, "description": "Owner Id (OwnerId)"},
                {"name": "created_at", "type": "datetime", "required": True, "description": "Created timestamp (CreatedDate)"},
            ],
            "business_rules": [
                {"rule": "Converted leads become Contact + Opportunity per org conversion mapping", "type": "process"},
            ],
        },
    },
    {
        "element_name": "Opportunity",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 23,
        "spec_data_seed": {
            "fields": [
                {"name": "id", "type": "string", "required": True, "description": "18-character record Id (Id)"},
                {"name": "name", "type": "string", "required": True, "description": "Opportunity name (Name)"},
                {"name": "account_id", "type": "string", "required": False, "description": "Account Id (AccountId)"},
                {"name": "stage_name", "type": "string", "required": True, "description": "Pipeline stage (StageName)"},
                {"name": "amount", "type": "decimal", "required": False, "description": "Amount (Amount)"},
                {"name": "probability", "type": "integer", "required": False, "description": "Probability % (Probability)"},
                {"name": "close_date", "type": "date", "required": True, "description": "Close date (CloseDate)"},
                {"name": "owner_id", "type": "string", "required": True, "description": "Owner Id (OwnerId)"},
                {"name": "created_at", "type": "datetime", "required": True, "description": "Created timestamp (CreatedDate)"},
            ],
            "business_rules": [
                {"rule": "Closed Won/Lost typically requires StageName in closed stages", "type": "process"},
            ],
        },
    },
    {
        "element_name": "Case",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 24,
        "spec_data_seed": {
            "fields": [
                {"name": "id", "type": "string", "required": True, "description": "18-character record Id (Id)"},
                {"name": "case_number", "type": "string", "required": True, "description": "Auto case number (CaseNumber)"},
                {"name": "subject", "type": "string", "required": True, "description": "Subject (Subject)"},
                {"name": "status", "type": "string", "required": True, "description": "Status (Status)"},
                {"name": "priority", "type": "string", "required": False, "description": "Priority (Priority)"},
                {"name": "account_id", "type": "string", "required": False, "description": "Account Id (AccountId)"},
                {"name": "contact_id", "type": "string", "required": False, "description": "Contact Id (ContactId)"},
                {"name": "owner_id", "type": "string", "required": True, "description": "Owner Id (OwnerId)"},
                {"name": "created_at", "type": "datetime", "required": True, "description": "Created timestamp (CreatedDate)"},
            ],
            "business_rules": [
                {"rule": "Escalation paths often keyed on Priority + Status", "type": "process"},
            ],
        },
    },
    {
        "element_name": "Order",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 25,
        "spec_data_seed": {
            "fields": [
                {"name": "id", "type": "string", "required": True, "description": "18-character record Id (Id)"},
                {"name": "order_number", "type": "string", "required": False, "description": "Order number (OrderNumber)"},
                {"name": "account_id", "type": "string", "required": True, "description": "Account Id (AccountId)"},
                {"name": "effective_date", "type": "date", "required": True, "description": "Effective date (EffectiveDate)"},
                {"name": "status", "type": "string", "required": True, "description": "Order status (Status)"},
                {"name": "total_amount", "type": "decimal", "required": False, "description": "Total amount (TotalAmount)"},
                {"name": "owner_id", "type": "string", "required": True, "description": "Owner Id (OwnerId)"},
                {"name": "created_at", "type": "datetime", "required": True, "description": "Created timestamp (CreatedDate)"},
            ],
            "business_rules": [
                {"rule": "Activation often requires Status transition from Draft", "type": "process"},
            ],
        },
    },
]

_GENERIC_HR_DATA_ENTITIES = [
    {
        "element_name": "Employee",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 20,
        "spec_data_seed": {
            "fields": [
                {"name": "employee_id",       "type": "string",   "required": True,  "description": "Unique employee number"},
                {"name": "first_name",        "type": "string",   "required": True,  "description": "Legal first name"},
                {"name": "last_name",         "type": "string",   "required": True,  "description": "Legal last name"},
                {"name": "email",             "type": "string",   "required": True,  "description": "Work email address"},
                {"name": "cost_centre",       "type": "string",   "required": True,  "description": "Primary cost centre assignment"},
                {"name": "grade",             "type": "string",   "required": False, "description": "Pay grade / job level"},
                {"name": "employment_type",   "type": "string",   "required": True,  "description": "FULL_TIME|PART_TIME|CONTRACTOR|FIXED_TERM"},
                {"name": "contract_type",     "type": "string",   "required": True,  "description": "PERMANENT|FIXED_TERM|ZERO_HOURS"},
                {"name": "start_date",        "type": "date",     "required": True,  "description": "Employment start date"},
                {"name": "end_date",          "type": "date",     "required": False, "description": "Employment end date (null if active)"},
                {"name": "probation_end_date","type": "date",     "required": False, "description": "Probation period end date"},
                {"name": "notice_period_days","type": "integer",  "required": False, "description": "Contractual notice period in days"},
                {"name": "manager_id",        "type": "string",   "required": False, "description": "Direct line manager employee_id"},
                {"name": "department",        "type": "string",   "required": True,  "description": "Organisational department"},
                {"name": "status",            "type": "string",   "required": True,  "description": "ACTIVE|ON_LEAVE|TERMINATED"},
            ],
            "business_rules": [
                {"rule": "end_date must be >= start_date when set", "type": "validation"},
                {"rule": "TERMINATED status requires end_date", "type": "validation"},
                {"rule": "payroll systems query status=ACTIVE only for salary runs", "type": "process"},
            ],
        },
    },
    {
        "element_name": "Position",
        "element_type": "DataObject",
        "archimate_layer": "Application",
        "mandatory": False,
        "display_order": 21,
        "spec_data_seed": {
            "fields": [
                {"name": "position_id",       "type": "string",   "required": True,  "description": "Unique position code"},
                {"name": "title",             "type": "string",   "required": True,  "description": "Job title"},
                {"name": "department",        "type": "string",   "required": True,  "description": "Owning department"},
                {"name": "cost_centre",       "type": "string",   "required": True,  "description": "Budget cost centre"},
                {"name": "grade",             "type": "string",   "required": False, "description": "Pay grade"},
                {"name": "headcount",         "type": "integer",  "required": True,  "description": "Approved headcount for this position"},
                {"name": "filled_count",      "type": "integer",  "required": True,  "description": "Currently filled seats"},
                {"name": "fte",               "type": "decimal",  "required": True,  "description": "Full-time equivalent budget"},
                {"name": "status",            "type": "string",   "required": True,  "description": "OPEN|FILLED|FROZEN|CLOSED"},
            ],
            "business_rules": [
                {"rule": "filled_count must not exceed headcount without budget override approval", "type": "validation"},
                {"rule": "FROZEN positions cannot be filled without finance sign-off", "type": "process"},
            ],
        },
    },
]

# Vendor key → domain entity list mapping for flask seed-entity-schemas
_DOMAIN_ENTITY_GROUPS = [
    ("SAP",                "2025.1", _SAP_DATA_ENTITIES),
    ("MICROSOFT_DYNAMICS", "2025.1", _MICROSOFT_DYNAMICS_DATA_ENTITIES),
    ("SALESFORCE",         "2025.1", _SALESFORCE_DATA_ENTITIES),
    ("GENERIC_HR",         "1.0",    _GENERIC_HR_DATA_ENTITIES),
]


@click.command("seed-entity-schemas")
@click.option("--dry-run", is_flag=True, help="Print what would be inserted without writing.")
@with_appcontext
def seed_entity_schemas(dry_run):
    """Populate domain DataObject field seeds on vendor_archimate_templates (idempotent upsert).

    These seeds pre-populate spec_data for recognised enterprise entities (Purchase Order,
    Invoice, Account, Employee, etc.) so architects confirm from validated schemas rather
    than LLM-inferred fields.
    """
    import json
    from app.models.vendor.vendor_organization import VendorArchiMateTemplate

    total_inserted = 0
    total_updated = 0
    total_skipped = 0

    for vendor_key, version, entities in _DOMAIN_ENTITY_GROUPS:
        click.echo(f"\n[{vendor_key} {version}] seeding {len(entities)} domain entities...")

        for spec in entities:
            seed_json = json.dumps(spec["spec_data_seed"])

            existing = VendorArchiMateTemplate.query.filter_by(
                vendor_key=vendor_key,
                element_name=spec["element_name"],
            ).first()

            if existing:
                if existing.spec_data_seed == seed_json:
                    total_skipped += 1
                    if dry_run:
                        click.echo(f"  [SKIP] {spec['element_name']} — seed unchanged")
                    continue
                # Update seed
                if dry_run:
                    click.echo(f"  [UPDATE] {spec['element_name']} — updating spec_data_seed")
                    total_updated += 1
                    continue
                existing.spec_data_seed = seed_json
                total_updated += 1
                continue

            if dry_run:
                click.echo(f"  [INSERT] {spec['element_name']} (new DataObject template)")
                total_inserted += 1
                continue

            tmpl = VendorArchiMateTemplate(
                vendor_key=vendor_key,
                element_name=spec["element_name"],
                element_type=spec["element_type"],
                archimate_layer=spec["archimate_layer"],
                mandatory=spec["mandatory"],
                version=version,
                display_order=spec["display_order"],
                spec_data_seed=seed_json,
            )
            db.session.add(tmpl)
            total_inserted += 1

    if not dry_run and (total_inserted > 0 or total_updated > 0):
        db.session.commit()

    click.echo("\n--- Summary ---")
    click.echo(f"  Inserted: {total_inserted}, Updated: {total_updated}, Skipped (unchanged): {total_skipped}")
    if dry_run:
        click.echo("(dry run — no changes written)")


@click.command("backfill-vendor-element-ids")
@click.option("--dry-run", is_flag=True, help="Print what would be changed without writing.")
@with_appcontext
def backfill_vendor_element_ids(dry_run):
    """Assign archimate_elements.id to VendorArchiMateTemplate rows that have element_id=NULL.

    For each template without an element_id:
    1. Look up archimate_elements by name — if found, reuse the existing element.
    2. If not found, create a new archimate_elements row (scope='enterprise', acm_source='vendor_template').
    3. Update VendorArchiMateTemplate.element_id.

    Idempotent: skips templates that already have element_id set.
    Required for populate_from_vendor() to create SolutionArchiMateElement entries at vendor link time.
    """
    from app.models.vendor.vendor_organization import VendorArchiMateTemplate

    # Resolve ArchiMateElement model (handles multiple import paths across codebase)
    ArchiMateElement = None
    for mod_path in ("app.models.archimate_core", "app.models.archimate", "app.models.archimate_models"):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            ArchiMateElement = getattr(mod, "ArchiMateElement", None)
            if ArchiMateElement is not None:
                # Verify it has the required columns
                if hasattr(ArchiMateElement, "id") and hasattr(ArchiMateElement, "name"):
                    break
        except ImportError:
            continue

    if ArchiMateElement is None:
        click.echo("[ERROR] Could not import ArchiMateElement model — aborting.", err=True)
        return

    templates_to_fix = VendorArchiMateTemplate.query.filter(
        VendorArchiMateTemplate.element_id.is_(None)
    ).order_by(VendorArchiMateTemplate.vendor_key, VendorArchiMateTemplate.display_order).all()

    if not templates_to_fix:
        click.echo("[OK] All VendorArchiMateTemplate rows already have element_id set.")
        return

    click.echo(f"Found {len(templates_to_fix)} templates with element_id=NULL.")

    # Layer name -> archimate layer string mapping
    _LAYER_MAP = {
        "Technology": "Technology",
        "Application": "Application",
        "Business": "Business",
        "Motivation": "Motivation",
    }

    linked = 0
    created = 0
    errors = 0

    for tmpl in templates_to_fix:
        try:
            # 1. Lookup by exact name
            elem = ArchiMateElement.query.filter(
                ArchiMateElement.name == tmpl.element_name
            ).first()

            if elem:
                click.echo(f"  [LINK]   {tmpl.vendor_key}/{tmpl.element_name} -> existing element id={elem.id}")
                if not dry_run:
                    tmpl.element_id = elem.id
                linked += 1
            else:
                # 2. Create new archimate_elements row
                layer = _LAYER_MAP.get(tmpl.archimate_layer, tmpl.archimate_layer or "Application")
                click.echo(f"  [CREATE] {tmpl.vendor_key}/{tmpl.element_name} (type={tmpl.element_type}, layer={layer})")
                if not dry_run:
                    # Resolve organization_id — use first org (default) as the canonical owner
                    try:
                        from app.models.organization import Organization
                        org = Organization.query.first()
                        org_id = org.id if org else 1
                    except Exception:
                        org_id = 1

                    new_elem = ArchiMateElement(
                        name=tmpl.element_name,
                        type=tmpl.element_type or "ApplicationComponent",
                        layer=layer,
                        description=f"Canonical {tmpl.vendor_key} element — auto-created by backfill-vendor-element-ids",
                        scope="enterprise",
                        acm_source="vendor_template",
                        organization_id=org_id,
                    )
                    db.session.add(new_elem)
                    db.session.flush()  # get new_elem.id without full commit
                    tmpl.element_id = new_elem.id
                created += 1

        except Exception as exc:
            click.echo(f"  [ERROR]  {tmpl.vendor_key}/{tmpl.element_name}: {exc}", err=True)
            errors += 1

    if not dry_run and (linked + created) > 0:
        try:
            db.session.commit()
            click.echo(f"\nDone — linked: {linked}, created: {created}, errors: {errors}")
        except Exception as exc:
            db.session.rollback()
            click.echo(f"[ERROR] Commit failed: {exc}", err=True)
    else:
        click.echo(f"\n{'(dry run) ' if dry_run else ''}linked: {linked}, created: {created}, errors: {errors}")


def init_app(app):
    """Register seed-entity-schemas and backfill-vendor-element-ids CLI commands."""
    app.cli.add_command(seed_entity_schemas)
    app.cli.add_command(backfill_vendor_element_ids)
