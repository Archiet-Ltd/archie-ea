"""The BIAN starter capability set a bank starts from: business areas, each with its service domains.

BIAN (the Banking Industry Architecture Network) publishes a service landscape of business areas and
service domains. This is the starter selection of that landscape: the business areas become
level 1 capabilities and the service domains under them level 2. Corporate Treasury is left out until its
place on the landscape is confirmed. It is a starting point to keep, change or remove, not the full landscape.
"""

BIAN_STARTER = (
    ("Sales and Service", (
        ("Customer Offer", "Making a tailored offer of products and services to a customer."),
        ("Customer Agreement", "Agreeing the terms under which a customer holds a product."),
        ("Sales Product", "Defining and managing the products the bank sells."),
        ("Customer Case", "Handling a customer's request or complaint from start to finish."),
        ("Party Authentication", "Checking that a customer or other party is who they say they are."),
    )),
    ("Operations and Execution", (
        ("Current Account", "Running a customer's day-to-day account."),
        ("Savings Account", "Running accounts that hold a customer's savings."),
        ("Consumer Loan", "Making and servicing loans to individuals."),
        ("Mortgage Loan", "Making and servicing loans secured on property."),
        ("Payment Order", "Taking in and validating an instruction to pay."),
        ("Payment Execution", "Carrying out a payment and settling it."),
        ("Position Keeping", "Keeping the running balance of every position the bank holds."),
        ("Card Authorization", "Approving or declining a card payment as it is made."),
    )),
    ("Risk and Compliance", (
        ("Customer Credit Rating", "Rating how likely a customer is to repay."),
        ("Fraud Evaluation", "Spotting transactions that look fraudulent."),
        ("Regulatory Compliance", "Meeting the rules that apply to the bank and showing it has."),
    )),
    ("Business Support", (
        ("Financial Accounting", "Keeping the bank's own books and producing its statements."),
        ("Products and Services Direction", "Setting the direction for the products and services the bank offers."),
    )),
)

# The stable code of each business area's row in the shared list of domains. It belongs to the area, not to
# its place in BIAN_STARTER, so reordering the list never renames a domain.
BIAN_AREA_CODES = {
    "Sales and Service": "BIAN-SS",
    "Operations and Execution": "BIAN-OE",
    "Risk and Compliance": "BIAN-RC",
    "Business Support": "BIAN-BS",
}
