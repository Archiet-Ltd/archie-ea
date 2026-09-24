"""Company-context intake: how the product reads a company's own public site.

This package owns the one public-page fetcher in the product
(:mod:`app.modules.company_context.services.public_page_fetcher`), the rules
that decide what is read and proposed (:mod:`app.modules.company_context
services.page_rules`, with the data files under ``app/seed_data
/company_context``) and, in later versions, the run, the proposals and the
review surface. This version builds the fetcher, the rules and their tests;
no route, blueprint, run record or model call exists yet.
"""