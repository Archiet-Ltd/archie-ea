"""What each ArchiMate 3.2 element MEANS, so the assistant can choose correctly.

The owner's goal for this product: architecture should not be exclusive to
organisations that can afford a team of architects. That requires the assistant
to model, not merely to discuss — and modelling is mostly *choosing the right
element*, not emitting a node.

A generic `create_archimate_element(type=...)` tool cannot do that. It accepts
whatever type string the model guesses, which hands the modelling judgement back
to the user — the exact expertise they do not have and cannot hire. So this
module carries, per element type: the ArchiMate 3.2 definition, when to use it,
and — the part that actually prevents wrong models — **when NOT to**, naming the
element that is usually confused with it.

`scripts/check_ai_layer_coverage.py` measured 54 of the product's 58 declared
element types with no dedicated AI creation path: technology 13, business 13,
application 9, motivation 6, implementation 5, strategy 4, physical 4. The
assistant could reason about motivation and design solutions, and could not
model the business, technology, strategy or migration layers — most of the
notation and most of the work.

Each entry here becomes one tool the model sees, with its own name and its own
description, generated in registry.py and executed through the single guarded
path in executor.py. One implementation, 58 distinct pieces of advice.

The `confused_with` field is deliberately the longest one in most entries. In
review after review, the commonest modelling error is not an invented element —
it is a Business Process where a Business Function belonged, or an Application
Service where an Application Component was meant. Getting that wrong produces a
model that validates and misleads, which is worse than an incomplete one.
"""

from __future__ import annotations

from typing import Dict

# The "layer" field here is descriptive only. scripts/check_ai_layer_coverage.py
# matches element-type keys between this table and _ELEMENT_TYPE_LAYER in
# app/models/archimate_core.py; it never compares the two "layer" values, so
# they are free to disagree, and currently do for the five Implementation &
# Migration types (this table says "implementation";
# _ELEMENT_TYPE_LAYER says "implementation_migration").
ELEMENT_SPECS: Dict[str, dict] = {
    # ----------------------------------------------------------------- #
    # Motivation — why the architecture exists.                          #
    # ----------------------------------------------------------------- #
    "stakeholder": {
        "layer": "motivation",
        "definition": (
            "The role of an individual, team or organisation that represents "
            "their interests in the architecture's outcome."
        ),
        "use_when": (
            "Someone or some group cares about the result and can influence it "
            "— a CFO worried about run cost, a regulator, a product owner."
        ),
        "confused_with": (
            "Not a business_actor. A stakeholder is a party with an INTEREST in "
            "the architecture; a business_actor is an entity that PERFORMS "
            "behaviour in the business. The same person can be both, modelled "
            "twice, for different reasons."
        ),
        "properties": ["name", "description", "concerns"],
    },
    "assessment": {
        "layer": "motivation",
        "definition": (
            "The outcome of analysing the state of affairs — a finding about "
            "the enterprise, usually a weakness, risk or opportunity."
        ),
        "use_when": (
            "You have concluded something about the current estate: 'order "
            "management cannot scale past 200 tps', 'three systems duplicate "
            "customer master data'."
        ),
        "confused_with": (
            "Not a driver. A driver is what the organisation cares about "
            "(cost, compliance); an assessment is what you FOUND when you "
            "examined it. Assessments normally realise or challenge drivers."
        ),
        "properties": ["name", "description", "severity"],
    },
    "outcome": {
        "layer": "motivation",
        "definition": "An end result that has been achieved.",
        "use_when": (
            "Naming a measurable end state the change is accountable for — "
            "'run cost reduced by 20%', 'single customer record in production'."
        ),
        "confused_with": (
            "Not a goal. A goal is a high-level statement of intent that may "
            "never be fully met ('be the most efficient operator'); an outcome "
            "is a concrete, measurable result that either happened or did not."
        ),
        "properties": ["name", "description", "measure", "target_value"],
    },
    "principle": {
        "layer": "motivation",
        "definition": (
            "A normative property that must hold for all architecture in scope."
        ),
        "use_when": (
            "Stating a rule every solution must obey — 'buy before build', "
            "'data is mastered once', 'no direct database integration'."
        ),
        "confused_with": (
            "Not a requirement or a constraint. A principle applies to ALL "
            "architecture; a requirement applies to a specific system; a "
            "constraint is a limitation you cannot change."
        ),
        "properties": ["name", "description", "rationale", "implications"],
    },
    "meaning": {
        "layer": "motivation",
        "definition": (
            "The knowledge or expertise carried by a concept, in a given "
            "context."
        ),
        "use_when": (
            "The interpretation of an element matters and differs between "
            "audiences — what 'active customer' means to finance versus "
            "marketing."
        ),
        "confused_with": (
            "Rarely needed. Model it only when a genuine semantic "
            "disagreement is causing an integration or reporting problem; "
            "otherwise a description on the element is enough."
        ),
        "properties": ["name", "description"],
    },
    "value": {
        "layer": "motivation",
        "definition": (
            "The relative worth, utility or importance of a concept."
        ),
        "use_when": (
            "Expressing what a capability or service is worth to whom — used "
            "on value streams and in investment discussions."
        ),
        "confused_with": (
            "Not an outcome. Value is worth to a stakeholder and is often "
            "qualitative; an outcome is a measured result."
        ),
        "properties": ["name", "description", "stakeholder"],
    },
    # ----------------------------------------------------------------- #
    # Business — who does what, for whom, and what they exchange.        #
    # ----------------------------------------------------------------- #
    "business_actor": {
        "layer": "business",
        "definition": (
            "A business entity that is capable of performing behaviour — a "
            "person, a team, a department, or a whole organisation. It is the "
            "'who', identified by who they ARE rather than what hat they wear."
        ),
        "use_when": (
            "Naming a real, identifiable party in the organisation or outside "
            "it: 'Claims Department', 'Acme Logistics Ltd', 'Jane Okafor'. If "
            "you could put it on an org chart or in a supplier list, it is an "
            "actor."
        ),
        "confused_with": (
            "Not a business_role. An actor is WHO exists; a role is the "
            "capacity in which someone acts. Ask: could this be reassigned to "
            "somebody else tomorrow without renaming it? 'Claims Assessor' can "
            "— that is a role. 'Claims Department' cannot — that is an actor. "
            "Model behaviour against the ROLE and assign actors to roles, so "
            "the model survives a reorganisation. Also not a stakeholder: a "
            "stakeholder holds an interest in the architecture, an actor "
            "performs business behaviour."
        ),
        "properties": ["name", "description", "actor_type", "parent_actor", "external"],
    },
    "business_collaboration": {
        "layer": "business",
        "definition": (
            "An aggregate of two or more roles that work together to perform "
            "behaviour neither performs alone."
        ),
        "use_when": (
            "Behaviour genuinely requires several roles acting jointly and you "
            "want to name the grouping — 'Credit Committee' (Underwriter + "
            "Risk Officer + Relationship Manager), 'Joint Venture Board'."
        ),
        "confused_with": (
            "Not a business_actor, even though it looks like a team. A "
            "collaboration is defined by the ROLES that make it up and exists "
            "only for the duration of the joint work; an actor exists in its "
            "own right on the org chart. Ask: if you removed the joint work, "
            "would this thing still exist? If yes it is an actor. Also "
            "distinct from business_interaction: the collaboration is the "
            "collective PERFORMER, the interaction is the collective "
            "BEHAVIOUR it performs. You usually need both."
        ),
        "properties": ["name", "description", "participating_roles"],
    },
    "business_event": {
        "layer": "business",
        "definition": (
            "Something that happens at a point in time and that business "
            "behaviour reacts to or produces. It has no duration and no "
            "internal steps."
        ),
        "use_when": (
            "A trigger or a notable occurrence starts or interrupts work — "
            "'Claim submitted', 'Payment received', 'Contract expiry date "
            "reached', 'Regulation came into force'."
        ),
        "confused_with": (
            "Not a business_process. Ask: does it TAKE TIME and have steps? "
            "'Handle claim' takes time — process. 'Claim received' is "
            "instantaneous — event. The common error is modelling the trigger "
            "and the work it triggers as one process, which hides who is "
            "waiting on what. Name events in the past tense so the "
            "distinction stays visible."
        ),
        "properties": ["name", "description", "trigger_source", "event_type"],
    },
    "business_function": {
        "layer": "business",
        "definition": (
            "A grouping of business behaviour based on the skills, resources "
            "or knowledge it requires, regardless of when or in what order it "
            "is carried out."
        ),
        "use_when": (
            "Naming an ongoing area of work that the organisation is always "
            "doing — 'Underwriting', 'Financial Accounting', 'Marketing', "
            "'Fleet Maintenance'. Functions are the stable skeleton; they "
            "change far less often than processes do."
        ),
        "confused_with": (
            "This is the single most common modelling error in ArchiMate. A "
            "business_process is a SEQUENCE of behaviour that runs and ends, "
            "producing a specific outcome; a business_function GROUPS "
            "behaviour by the capability it requires, with no order and no "
            "end. Ask: does ORDER matter, and is there a finishing line? "
            "'Handle a motor claim end to end' has both — process. 'Claims "
            "Handling' as a permanent competence the company possesses has "
            "neither — function. If a name reads like a department or a skill "
            "it is a function; if it reads like a verb phrase you could draw a "
            "flowchart of, it is a process. Not a capability either: a "
            "capability (strategy layer) is what the organisation is ABLE to "
            "do; a function is behaviour actually grouped and performed."
        ),
        "properties": ["name", "description", "owning_role", "parent_function"],
    },
    "business_interaction": {
        "layer": "business",
        "definition": (
            "A unit of business behaviour performed jointly by two or more "
            "roles working as a collaboration."
        ),
        "use_when": (
            "The work is genuinely done together and cannot be split into one "
            "party's steps — 'Negotiate contract terms', 'Conduct joint credit "
            "review', 'Hold arbitration hearing'."
        ),
        "confused_with": (
            "Not a business_process. A process is performed by ONE role or "
            "actor (even if it hands off between several in sequence); an "
            "interaction is performed by several roles SIMULTANEOUSLY, as one "
            "act. Ask: could you split this into 'A does this, then B does "
            "that' without losing its meaning? If yes it is a process with "
            "hand-offs, not an interaction. Negotiation cannot be split — both "
            "parties are negotiating at once — so it is an interaction. An "
            "interaction is performed by a business_collaboration; if no "
            "collaboration exists, you probably want a process."
        ),
        "properties": ["name", "description", "performing_collaboration"],
    },
    "business_interface": {
        "layer": "business",
        "definition": (
            "A point of access where a business service is made available — "
            "the channel through which a role or actor is reached."
        ),
        "use_when": (
            "Naming HOW a service is consumed: 'Branch counter', 'Call "
            "centre', 'Customer web portal', 'Post'. The same service is "
            "usually offered through several interfaces."
        ),
        "confused_with": (
            "Not a business_service. The service is WHAT is offered ('Account "
            "opening'); the interface is WHERE and HOW you get at it ('Branch "
            "counter'). Ask: could I offer the same thing through a different "
            "channel tomorrow? Then the thing offered is the service and the "
            "channel is the interface. Modelling channels as services is the "
            "usual error and it duplicates the same offering once per channel. "
            "Also not an application_interface: this one is reached by people "
            "and organisations, not by software."
        ),
        "properties": ["name", "description", "channel", "exposed_service"],
    },
    "business_object": {
        "layer": "business",
        "definition": (
            "A concept used within the business that has meaning independently "
            "of how it is stored or displayed — the information itself."
        ),
        "use_when": (
            "Naming something the business talks about and needs to keep track "
            "of: 'Customer', 'Insurance Policy', 'Invoice', 'Claim'."
        ),
        "confused_with": (
            "Three neighbours, and the discriminating question differs for "
            "each. (1) A representation is a perceptible FORM of the object — "
            "the printed policy schedule, the PDF invoice, the paper claim "
            "form. Ask: can you hold it, read it, or send it? Then it is a "
            "representation, not the object. (2) A data_object (application "
            "layer) is the object as a system stores it; the business_object "
            "is the business meaning. One business object is often realised by "
            "several data objects across systems — that mismatch is exactly "
            "what the model is for. (3) A product bundles services and a "
            "contract into something sold: 'Insurance Policy' as the sellable "
            "offering is a product, as the record of the agreement it is a "
            "business_object."
        ),
        "properties": ["name", "description", "owning_function", "classification"],
    },
    "business_process": {
        "layer": "business",
        "definition": (
            "A sequence of business behaviour that achieves a specific "
            "outcome. It starts, it runs in an order, it finishes, and "
            "something of value exists at the end."
        ),
        "use_when": (
            "You can describe the work as a flow with a beginning, an end and "
            "a result — 'a customer complaint is handled end to end, from "
            "logging through investigation to resolution letter', 'Order to "
            "cash', 'Onboard a new employee'."
        ),
        "confused_with": (
            "Confused with a business_function, and getting this backwards is "
            "the commonest "
            "error in the notation. A process is a SEQUENCE of behaviour "
            "producing a specific outcome; a function GROUPS behaviour by the "
            "capability it requires, regardless of sequence — ask whether "
            "ORDER MATTERS. If reordering the steps would break it, it is a "
            "process. 'Underwriting' as a permanent area of expertise is a "
            "function; 'Underwrite a motor policy application' is a process "
            "that the underwriting function performs. Also not a "
            "business_service: the process is the internal work, the service "
            "is what the outside sees of it. And not a business_interaction: "
            "if two roles perform the behaviour jointly rather than in "
            "sequence, it is an interaction."
        ),
        "properties": [
            "name",
            "description",
            "trigger_event",
            "outcome",
            "performing_role",
            "parent_process",
        ],
    },
    "business_role": {
        "layer": "business",
        "definition": (
            "The responsibility for performing specific behaviour, to which an "
            "actor can be assigned — the hat, not the head."
        ),
        "use_when": (
            "Naming a responsibility that behaviour hangs off and that people "
            "or teams get assigned to: 'Claims Assessor', 'Data Owner', "
            "'Approver', 'Supplier'."
        ),
        "confused_with": (
            "Confused with a business_actor. The role is the RESPONSIBILITY; "
            "the actor is "
            "the party that holds it. Ask: if this person or team left, would "
            "the name still be meaningful? 'Approver' would — role. 'Jane "
            "Okafor' would not — actor. Always attach behaviour to roles and "
            "assign actors to roles, so a reorganisation changes assignments "
            "rather than the whole model. Not a business_collaboration "
            "either: that is two or more roles combined to act as one."
        ),
        "properties": ["name", "description", "responsibilities", "assigned_actor"],
    },
    "business_service": {
        "layer": "business",
        "definition": (
            "Explicitly defined behaviour that a business offers to its "
            "environment, described in terms of what the consumer gets — never "
            "in terms of how it is produced."
        ),
        "use_when": (
            "Something is offered to a customer or to another part of the "
            "business as a usable whole — 'Account opening', 'Claims "
            "settlement', 'Payroll service to subsidiaries'."
        ),
        "confused_with": (
            "Confused with a business_process. The service is the OUTSIDE view "
            "— what a "
            "consumer can ask for and what they get; the process is the INSIDE "
            "view — the steps that produce it. Ask: would a customer recognise "
            "this name and want it? Then it is a service. Would only the "
            "people doing the work recognise it? Then it is a process. Every "
            "service should be realised by a process, function or interaction; "
            "a service with nothing behind it is a promise the model cannot "
            "keep. Also not a business_interface: the interface is the channel "
            "(branch, portal) through which the service is reached. And not a "
            "product: a product bundles services with a contract into "
            "something sold."
        ),
        "properties": ["name", "description", "consumer", "realising_process", "sla"],
    },
    "contract": {
        "layer": "business",
        "definition": (
            "A formal or informal specification of an agreement between "
            "parties, setting out the rights and obligations attached to a "
            "product or service. It is a specialisation of business_object."
        ),
        "use_when": (
            "The terms of an agreement matter in their own right — a Master "
            "Services Agreement, an SLA, 'Terms and Conditions', an employment "
            "contract, an internal service agreement between departments."
        ),
        "confused_with": (
            "Confused with a business_object and with a product. A contract IS "
            "a business_object, "
            "but the more specific one — use it whenever the object records "
            "obligations between parties, because that specialisation is what "
            "makes governance and compliance views possible. Against product: "
            "the product is the whole offering (services plus contract) that "
            "the customer buys; the contract is only the agreement governing "
            "it. Ask: does it state what each side must do? Then contract. Is "
            "it the thing on the price list? Then product."
        ),
        "properties": ["name", "description", "parties", "valid_from", "valid_to"],
    },
    "product": {
        "layer": "business",
        "definition": (
            "A coherent collection of services and/or passive elements, "
            "together with a contract, offered as a whole to customers."
        ),
        "use_when": (
            "Naming what the organisation actually sells or offers as a "
            "package — 'Comprehensive Motor Insurance', 'Business Current "
            "Account', 'Gold Support Plan'."
        ),
        "confused_with": (
            "Confused with a business_service. A product is a BUNDLE — one or "
            "more services "
            "plus the contract and price under which they are offered; a "
            "service is a single unit of offered behaviour. Ask: does it "
            "appear on a price list or in a catalogue with terms attached? "
            "Then product. Is it one thing the business does for you? Then "
            "service. Modelling every product as a service loses the "
            "commercial packaging, which is usually the part the business "
            "cares about most. Also not a business_object: the object is the "
            "information (the policy record), the product is the offering."
        ),
        "properties": ["name", "description", "bundled_services", "contract", "pricing"],
    },
    "representation": {
        "layer": "business",
        "definition": (
            "A perceptible form of the information carried by a business "
            "object — how that information appears to a human being."
        ),
        "use_when": (
            "The physical or visible form matters, usually because it is "
            "regulated, printed, signed or sent — 'Printed policy schedule', "
            "'PDF invoice', 'Paper claim form', 'Annual report'."
        ),
        "confused_with": (
            "Confused with a business_object. The object is the MEANING "
            "('Invoice' as a "
            "concept the business reasons about); the representation is a "
            "perceptible FORM of it ('PDF invoice', 'posted paper invoice'). "
            "Ask: can a person see, hold or read this particular thing? Then "
            "it is a representation. One business_object can have several "
            "representations, and that is precisely when to model them — the "
            "same invoice posted, emailed and shown in a portal. If there is "
            "only one form and nobody cares about it, model just the "
            "business_object. Also not a data_object: that is the system's "
            "stored form, not the human-perceptible one."
        ),
        "properties": ["name", "description", "represented_object", "medium", "format"],
    },
    # ----------------------------------------------------------------- #
    # Technology — the infrastructure and platform that runs the         #
    # applications. The layer where models most often go wrong, because  #
    # four of its elements (node, device, system software, artifact) all #
    # feel like "a server" to someone describing their estate.           #
    # ----------------------------------------------------------------- #
    "artifact": {
        "layer": "technology",
        "definition": (
            "A piece of data used or produced in a software development "
            "process, or by deployment and operation of a system — a "
            "physical, deployable file."
        ),
        "use_when": (
            "Naming the thing that actually sits on a disk and gets deployed: "
            "a container image, a .jar or .war, a database dump, a Terraform "
            "state file, a firmware binary, a CSV a nightly job writes."
        ),
        "confused_with": (
            "Not an application_component, and this is the single most common "
            "technology-layer error. Ask: could you copy it to a USB stick? "
            "If yes it is an artifact — 'payments-api:2.7.1' the image. If it "
            "is instead the software element that behaves, holds state and "
            "offers services, it is an application_component — 'Payments "
            "API'. The relationship between them is realisation: the artifact "
            "REALISES the component. Model both only when the deployment "
            "itself is the subject (release trains, node assignment, "
            "provenance); if you only care about what the software does, one "
            "application_component is enough. Also not a data_object: a "
            "data_object is the logical information ('Customer record'); the "
            "artifact is the file that carries it ('customers.parquet')."
        ),
        "properties": ["name", "description", "artifact_kind", "location", "version"],
    },
    "communication_network": {
        "layer": "technology",
        "definition": (
            "A set of structures and behaviours that connects computer "
            "systems or other electronic devices for transmission, routing "
            "and reception of data."
        ),
        "use_when": (
            "Modelling an actual network you own, operate or buy: the "
            "corporate MPLS WAN, a store's LAN, an Azure VNet, a factory's "
            "OT segment, the 4G APN the handhelds use."
        ),
        "confused_with": (
            "Not a path. The discriminating question is: does it have kit and "
            "an owner? A communication_network is the concrete medium — "
            "switches, links, address ranges, a supplier contract. A path is "
            "the LOGICAL 'these two nodes can talk to each other' link, with "
            "no statement about how. Draw the path when the point is that the "
            "connection exists and matters; add the communication_network "
            "when the point is which network carries it, and the path is then "
            "realised by the network. If your element name contains 'VLAN', "
            "'VPN', 'WAN' or a CIDR block it is a communication_network; if "
            "it is 'app server to database' it is a path."
        ),
        "properties": ["name", "description", "bandwidth", "network_type", "latency"],
    },
    "device": {
        "layer": "technology",
        "definition": (
            "A physical IT resource upon which system software and artifacts "
            "may be stored or deployed."
        ),
        "use_when": (
            "The physical box is architecturally significant — a specific "
            "blade or rack server, a POS terminal, a PLC or gateway on the "
            "shop floor, a handheld scanner, a firewall appliance, a "
            "site-resident NAS."
        ),
        "confused_with": (
            "Not a node, though every device IS a specialised node. Ask: does "
            "it have a power cable and a serial number? If yes, device. If it "
            "is a virtual machine, a Kubernetes cluster, an EC2 instance or "
            "'the production environment', it is a node — you cannot point at "
            "it. And not system_software: the device is the metal, the "
            "system_software is the environment running ON the metal (the "
            "hypervisor, OS, DBMS). A useful test for cloud estates — if a "
            "supplier owns the hardware and you rent capacity, model a node, "
            "not a device, because the physical unit is not yours to reason "
            "about. Also not equipment (physical layer): a device processes "
            "data; equipment acts on physical material."
        ),
        "properties": ["name", "description", "device_type", "location", "vendor"],
    },
    "node": {
        "layer": "technology",
        "definition": (
            "A computational or physical resource that hosts, manipulates or "
            "interacts with other computational or physical resources."
        ),
        "use_when": (
            "The default technology structure element, and usually the right "
            "one: a VM, a cluster, an environment, a managed platform, a "
            "logical 'application server' you have not decided the hardware "
            "for yet."
        ),
        "confused_with": (
            "A node is any computational resource; a DEVICE is physical "
            "hardware you could point at; SYSTEM SOFTWARE is the environment "
            "running on it — ask whether it has a power cable (device), "
            "whether it is a program you could upgrade (system software), or "
            "neither, in which case it is a node. When in doubt use node: it "
            "is the general case and both others specialise it, so a node is "
            "never WRONG, only sometimes less precise. Not an "
            "application_component either — a node hosts software, it is not "
            "the software; 'Order Service' running on 'prod-k8s' is a "
            "component assigned to a node."
        ),
        "properties": ["name", "description", "node_type", "environment", "location"],
    },
    "path": {
        "layer": "technology",
        "definition": (
            "A link between two or more nodes, through which they exchange "
            "data or material."
        ),
        "use_when": (
            "You need to say two nodes are connected and the connection "
            "carries architectural weight — a cross-site replication link, "
            "the hop from DMZ to core, a store-to-datacentre connection whose "
            "loss stops trading."
        ),
        "confused_with": (
            "Not a communication_network. A path is the logical connection "
            "('these two can exchange data'); the network is the concrete "
            "medium that realises it. If you can name the supplier, the "
            "bandwidth or the address range, you have described the network, "
            "not the path. Also not a flow relationship: a flow says data "
            "moves between two behaviours; a path says an infrastructure "
            "route exists between two structures, whether or not anything is "
            "currently using it."
        ),
        "properties": ["name", "description", "protocol", "source_node", "target_node"],
    },
    "system_software": {
        "layer": "technology",
        "definition": (
            "Software that provides or contributes to an environment for "
            "storing, executing and using software or data."
        ),
        "use_when": (
            "The platform software is a thing you version, patch, license or "
            "get audited on: an operating system, a DBMS, a hypervisor, a Java "
            "runtime, a message broker, a web server, a container runtime."
        ),
        "confused_with": (
            "Not an application_component. Ask who it serves: system_software "
            "serves other SOFTWARE (it is an execution environment — Postgres "
            "16, RHEL 9, Kafka); an application_component serves a BUSINESS "
            "process (it does something the organisation would recognise — "
            "'Claims Assessment'). A packaged product can be either depending "
            "on the role it plays: SAP is an application_component; the HANA "
            "database under it is system_software. And not a device: system "
            "software runs on hardware, it is not hardware. If you are "
            "tracking an end-of-support date or a CVE, it is almost always "
            "system_software."
        ),
        "properties": ["name", "description", "vendor", "version", "support_status"],
    },
    "technology_collaboration": {
        "layer": "technology",
        "definition": (
            "An aggregate of two or more nodes that work together to perform "
            "collective technology behaviour."
        ),
        "use_when": (
            "Two or more nodes must act together for the behaviour to exist "
            "and neither owns it alone — a database cluster's quorum members, "
            "a pair of load-balanced firewalls, a blockchain node set, an "
            "active-active site pair."
        ),
        "confused_with": (
            "Not a node with children. Use composition when one node simply "
            "CONTAINS others (a cluster made of VMs you administer as one "
            "thing); use technology_collaboration only when the point is that "
            "separately-owned or separately-modelled nodes COOPERATE, and the "
            "cooperation itself performs behaviour you want to name. If you "
            "cannot name the collective behaviour (a "
            "technology_interaction), you do not need a collaboration. This "
            "element is genuinely rare — most estates never need one."
        ),
        "properties": ["name", "description", "participants"],
    },
    "technology_event": {
        "layer": "technology",
        "definition": "A technology state change.",
        "use_when": (
            "Something happens at the infrastructure level that behaviour "
            "reacts to and it matters architecturally — 'disk 90% full', "
            "'node failover', 'certificate expired', 'backup completed', "
            "'message arrived on queue'."
        ),
        "confused_with": (
            "Not a technology_process. An event is instantaneous and has no "
            "duration — it is the trigger; a process takes time and is what "
            "the trigger sets off. 'Certificate expired' is the event; 'renew "
            "and redeploy certificate' is the process. If you can ask 'how "
            "long does it take?' and get an answer, it is not an event. Also "
            "not an application_event: this one is raised by infrastructure, "
            "not by application logic."
        ),
        "properties": ["name", "description", "trigger", "severity"],
    },
    "technology_function": {
        "layer": "technology",
        "definition": (
            "A collection of technology behaviour that can be performed by a "
            "node, grouped by chosen criteria such as required skills or "
            "resources."
        ),
        "use_when": (
            "Naming what a node is CAPABLE of, independent of any particular "
            "run — 'message routing', 'data replication', 'TLS termination', "
            "'batch scheduling'."
        ),
        "confused_with": (
            "Not a technology_process, and the discriminator is the same one "
            "as in the business layer: a function is grouped by CAPABILITY "
            "and has no start or end; a process is grouped by SEQUENCE and "
            "runs from a trigger to a result. 'Backup' as something the "
            "storage platform can do is a function; 'nightly backup run, 02:00 "
            "to 04:30' is a process. And not a technology_service: the "
            "function is the internal behaviour; the service is what is "
            "EXPOSED to a consumer outside the node."
        ),
        "properties": ["name", "description", "performed_by"],
    },
    "technology_interaction": {
        "layer": "technology",
        "definition": (
            "A unit of collective technology behaviour performed by two or "
            "more nodes."
        ),
        "use_when": (
            "The behaviour genuinely requires more than one node acting "
            "together and cannot be attributed to either — a two-phase commit, "
            "a Raft leader election, a mutual-TLS handshake between peers."
        ),
        "confused_with": (
            "Not a technology_process performed by one node that happens to "
            "call another. Ask: if you removed one participant, would the "
            "behaviour still be describable as itself? If yes it is a process "
            "on one node with a serving relationship to the other; if no — "
            "there is no election with one voter — it is an interaction. "
            "Requires a technology_collaboration to perform it. Rare; do not "
            "reach for it to model ordinary request/response."
        ),
        "properties": ["name", "description", "participants"],
    },
    "technology_interface": {
        "layer": "technology",
        "definition": (
            "A point of access where technology services offered by a node "
            "can be accessed."
        ),
        "use_when": (
            "The access point itself is architecturally significant — a "
            "published port and protocol, a JDBC endpoint, an SFTP drop, a "
            "management console URL, a physical console port."
        ),
        "confused_with": (
            "Not a technology_service. The service is WHAT is offered "
            "('relational data storage'); the interface is WHERE and HOW you "
            "reach it ('postgres://prod-db:5432, TLS required'). Two "
            "interfaces can expose the same service, and that is exactly when "
            "you should model interfaces at all — a legacy SOAP endpoint and a "
            "new REST one over one service is worth drawing; one endpoint per "
            "service is noise. If your element name is a URL, a port or a "
            "protocol, it is an interface."
        ),
        "properties": ["name", "description", "protocol", "endpoint", "access_type"],
    },
    "technology_process": {
        "layer": "technology",
        "definition": (
            "A sequence of technology behaviours that achieves a specific "
            "result."
        ),
        "use_when": (
            "There is an ordered infrastructure activity with a trigger and an "
            "outcome — a deployment pipeline run, a nightly ETL load, a "
            "failover procedure, a patch cycle, a certificate rotation."
        ),
        "confused_with": (
            "Not a technology_function: a process is a SEQUENCE with a start "
            "and an end; a function is a grouping of capability with neither. "
            "'Restore from backup' (steps, ends in a restored system) is a "
            "process; 'backup and recovery' (what the platform can do) is a "
            "function. And not a work_package: a process is repeatable "
            "operational behaviour of the running estate; a work_package is a "
            "one-off piece of change with a budget and an end date."
        ),
        "properties": ["name", "description", "trigger", "outcome", "schedule"],
    },
    "technology_service": {
        "layer": "technology",
        "definition": (
            "An explicitly defined exposed technology behaviour — what a node "
            "offers to its environment."
        ),
        "use_when": (
            "Naming what infrastructure provides to applications or to other "
            "infrastructure, in the consumer's terms — 'relational database "
            "hosting', 'container orchestration', 'authentication', 'object "
            "storage', 'network file share'."
        ),
        "confused_with": (
            "Not a technology_interface: the service is WHAT is offered, the "
            "interface is the access point where you get it — if you can name "
            "a port, you are describing the interface. Not an "
            "application_service either: the discriminating question is who "
            "the consumer is — a technology_service serves applications and "
            "other infrastructure ('message queuing'); an application_service "
            "serves the business ('submit claim'). And not a "
            "technology_function: the function is internal behaviour the node "
            "performs; the service is the subset it deliberately EXPOSES. If "
            "nobody outside the node can consume it, it is a function."
        ),
        "properties": ["name", "description", "consumer", "service_level"],
    },
    # ----------------------------------------------------------------- #
    # Physical — the layer for estates where the architecture moves      #
    # atoms, not only bits. Specified rather than hatched: see the note  #
    # below.                                                             #
    #                                                                    #
    # These four are the ones an EA tool is most often told to skip, and #
    # the checker offers an `ai-layer-ok` hatch for exactly that. Not    #
    # taken here, for a product-specific reason: Entelim already carries  #
    # manufacturing-specific fields (`manufacturing_capabilities`,       #
    # `shop_floor_system`), so the estates it is being pointed at are    #
    # precisely those where an OT/IT boundary — a PLC on a line, a       #
    # plant, a pipeline — is the interesting part of the architecture.   #
    # Hatching would have told a manufacturing user that the layer       #
    # describing their business is out of scope for the assistant, while #
    # the schema underneath invites them to model it. Four entries is a  #
    # small price for not shipping that contradiction.                   #
    # ----------------------------------------------------------------- #
    "distribution_network": {
        "layer": "physical",
        "definition": (
            "A physical network used to transport materials or energy."
        ),
        "use_when": (
            "Physical movement between facilities is part of the "
            "architecture — a road freight lane, a rail link, a pipeline, a "
            "power distribution grid, a chilled-goods route between depot and "
            "store."
        ),
        "confused_with": (
            "Not a communication_network. The test is what travels: material "
            "or energy (distribution_network) versus data "
            "(communication_network). A fibre duct carrying data is a "
            "communication network; the same trench carrying gas is a "
            "distribution network. Not a business_process either: the network "
            "is the standing physical structure, the process is the "
            "behaviour that uses it — 'trunk route Rotterdam–Milan' versus "
            "'outbound shipment'."
        ),
        "properties": ["name", "description", "transport_mode", "capacity", "route"],
    },
    "equipment": {
        "layer": "physical",
        "definition": (
            "One or more physical machines, tools or instruments that can "
            "create, use, store, move or transform materials."
        ),
        "use_when": (
            "The machine is architecturally significant because it acts on "
            "physical things — a CNC machine, a filling line, a robot cell, a "
            "forklift, an MRI scanner, a chiller."
        ),
        "confused_with": (
            "Not a device, and this is the OT/IT boundary that matters in a "
            "plant. Ask what it acts on: equipment transforms MATERIAL; a "
            "device processes DATA. A robot arm is equipment; the PLC "
            "controlling it is a device; the two are usually both modelled "
            "and related. Modern kit blurs this — a CNC machine with an "
            "embedded controller is equipment, and you model its controller "
            "separately as a device only when its software, patching or "
            "network position is what you are reasoning about. Not a facility "
            "either: equipment sits inside a facility."
        ),
        "properties": ["name", "description", "equipment_type", "location", "capacity"],
    },
    "facility": {
        "layer": "physical",
        "definition": (
            "A physical structure or environment — a factory, warehouse, "
            "office, laboratory or data centre building."
        ),
        "use_when": (
            "The place itself carries architectural meaning: a plant whose "
            "closure is being modelled, a distribution centre, a store estate, "
            "a data-centre site subject to a migration or a residency rule."
        ),
        "confused_with": (
            "Not a node. A facility is a BUILDING or site; a node is a "
            "computational resource. 'DC-London' as a building with a lease "
            "and a power feed is a facility; 'DC-London' as the compute "
            "capacity you deploy onto is a node — if you are about to attach "
            "VMs to it you meant the node. Also not a business_actor: 'Leeds "
            "Plant' as the organisational unit that performs work is a "
            "business_actor; as the site it occupies, a facility. Model both "
            "only when a reorganisation or a site move would separate them."
        ),
        "properties": ["name", "description", "facility_type", "location", "site_code"],
    },
    "material": {
        "layer": "physical",
        "definition": (
            "Tangible physical matter or physical elements — raw material, "
            "components, finished goods, energy."
        ),
        "use_when": (
            "The physical thing flowing through the estate is what you are "
            "reasoning about — steel coil into a press, a blood sample "
            "through a lab, a pallet of finished goods, fuel."
        ),
        "confused_with": (
            "Not a business_object and not a data_object. The test is "
            "tangibility: material is matter you could weigh; a "
            "business_object is information the business recognises ('Order'); "
            "a data_object is its representation in a system. The pallet is "
            "material; the despatch note about it is a business_object; the "
            "row in the WMS is a data_object. Modelling the information as "
            "material is the usual error, and it makes flows through the "
            "physical layer meaningless."
        ),
        "properties": ["name", "description", "material_type", "unit_of_measure"],
    },
    # ----------------------------------------------------------------- #
    # Application - the software that supports the business.             #
    # ----------------------------------------------------------------- #
    "application_collaboration": {
        "layer": "application",
        "definition": (
            "An aggregate of two or more application components that work "
            "together to perform collective application behaviour."
        ),
        "use_when": (
            "Behaviour genuinely belongs to a SET of systems and to no one of "
            "them - 'the payments platform' as the ERP plus the gateway plus "
            "the reconciliation engine, none of which settles a payment alone."
        ),
        "confused_with": (
            "Not an application_component, and not a folder. A collaboration "
            "owns no code and can be dissolved by re-drawing the boundary; a "
            "component is a deployable thing with an owner and a lifecycle. "
            "The test: if you decommission the collaboration, is anything "
            "actually switched off? If no, it is a collaboration. If yes, you "
            "meant a component. Also not an application_interaction - the "
            "collaboration is WHO cooperates, the interaction is WHAT they do "
            "together; you usually need both or neither."
        ),
        "properties": ["name", "description", "participating_components"],
    },
    "application_component": {
        "layer": "application",
        "definition": (
            "An encapsulated unit of software functionality with a "
            "well-defined interface, independently deployable and replaceable."
        ),
        "use_when": (
            "Naming a system that EXISTS and someone owns - Salesforce, the "
            "billing engine, an in-house Django service. If it appears in the "
            "application portfolio, has a licence, a version or a support "
            "contact, it is a component."
        ),
        "confused_with": (
            "Not an application_service. The component is the THING that "
            "exists; the service is what it offers to others, named from the "
            "consumer's side. 'Salesforce' is a component; 'lead qualification "
            "service' is a service Salesforce realises. The test: could you "
            "replace the product behind it and keep the same promise to "
            "consumers? Then the promise is the service and the product is the "
            "component. Also not an application_interface - the interface is "
            "the socket the service is offered THROUGH (a REST API, a file "
            "drop), not the software behind it. Model the component when the "
            "conversation is about ownership, cost, lifecycle or replacement; "
            "model the service when it is about dependency and consumption."
        ),
        "properties": ["name", "description", "vendor", "lifecycle_status"],
    },
    "application_event": {
        "layer": "application",
        "definition": (
            "An application behaviour element that denotes a state change; it "
            "happens at a point in time and has no duration."
        ),
        "use_when": (
            "Something instantaneous triggers or is emitted by application "
            "behaviour - 'PaymentAuthorised', 'record updated', a webhook, a "
            "message on a topic. Event-driven and integration architectures "
            "are mostly these."
        ),
        "confused_with": (
            "Not an application_process. A process TAKES time and can be "
            "interrupted; an event is a zero-duration fact that starts or ends "
            "one. 'Settle payment' is a process; 'payment settled' is the "
            "event it emits. If you can ask 'how long does it take?' and get a "
            "sensible answer, it is not an event. Also not an "
            "application_service: an event is a notification, a service is a "
            "standing offer of behaviour a consumer can invoke."
        ),
        "properties": ["name", "description", "trigger_source"],
    },
    "application_function": {
        "layer": "application",
        "definition": (
            "Automated behaviour grouped by the coherent set of skills or "
            "resources that performs it, rather than by the order it runs in."
        ),
        "use_when": (
            "Describing WHAT a system can do, timeless and without sequence - "
            "'pricing calculation', 'document rendering', 'fraud scoring'. "
            "Functions are how you decompose a component's insides to compare "
            "two systems' overlap, so they are the right element for "
            "rationalisation and duplication analysis."
        ),
        "confused_with": (
            "Not an application_process. Same behaviour, different grouping: a "
            "function is grouped by CAPABILITY (what it takes to do it) and "
            "has no beginning or end; a process is grouped by SEQUENCE (this "
            "step, then that one) and delivers a specific result. 'Fraud "
            "scoring' is a function; 'screen a claim, then score it, then "
            "route it' is a process. If naming it forced you to say 'then', "
            "you have a process. Also not an application_service - a function "
            "is internal and may never be exposed; a service is the part "
            "deliberately made visible to others."
        ),
        "properties": ["name", "description", "performing_component"],
    },
    "application_interaction": {
        "layer": "application",
        "definition": (
            "Behaviour performed jointly by two or more application components "
            "in collaboration."
        ),
        "use_when": (
            "The unit of behaviour cannot be attributed to a single system "
            "because it only exists in the exchange - a two-phase commit, a "
            "reconciliation handshake, an SSO token exchange."
        ),
        "confused_with": (
            "Not an application_process performed by one component that "
            "happens to call another. Calling is normal and stays a process; "
            "an interaction is for behaviour where BOTH parties are performers "
            "and neither could be said to be doing it alone. If you can name "
            "one owner who would be accountable for it, model a process and a "
            "serving relationship instead - that is the answer far more often, "
            "and an over-used interaction makes a model unreadable."
        ),
        "properties": ["name", "description", "participating_components"],
    },
    "application_interface": {
        "layer": "application",
        "definition": (
            "A point of access where application services are made available "
            "to another component or to a role."
        ),
        "use_when": (
            "Naming the actual channel - 'Orders REST API v2', 'the nightly "
            "SFTP drop', 'the ODBC endpoint', 'the web UI'. Interfaces are "
            "what integration work and API governance actually attach to."
        ),
        "confused_with": (
            "Not an application_service. The service is the PROMISE (what a "
            "consumer gets); the interface is the DOOR it is delivered "
            "through. One service is often offered through several interfaces "
            "- the same 'customer lookup' over REST, over a batch file, and "
            "through a screen - and each has its own contract, version and "
            "consumers, which is exactly why they are separate elements. If "
            "you find yourself writing a URL, a protocol or a file format, you "
            "are describing an interface. Also not a technology_interface: "
            "this is software-to-software, not a physical or infrastructure "
            "port."
        ),
        "properties": ["name", "description", "protocol", "exposing_component"],
    },
    "application_process": {
        "layer": "application",
        "definition": (
            "A sequence of automated behaviours that achieves a specific "
            "result."
        ),
        "use_when": (
            "The ORDER matters and the result is identifiable - 'nightly "
            "settlement run', 'onboard a customer record', an ETL pipeline, an "
            "orchestrated saga."
        ),
        "confused_with": (
            "Not an application_function (function = grouped by ability, no "
            "sequence; process = grouped by sequence, one result). Not a "
            "business_process either: an application process is performed by "
            "SOFTWARE. If a person can be slow at it, it is a business "
            "process; if only a machine performs it, it is an application "
            "process. The commonest error is modelling a business process and "
            "labelling it with the system's name."
        ),
        "properties": ["name", "description", "performing_component", "trigger"],
    },
    "application_service": {
        "layer": "application",
        "definition": (
            "An explicitly defined, exposed application behaviour with a "
            "value meaningful to its consumer."
        ),
        "use_when": (
            "Naming what one system promises to others, in the CONSUMER's "
            "words and independent of who supplies it - 'customer credit "
            "check', 'address validation', 'send statement'. This is the "
            "element that makes dependency and impact analysis work, because "
            "it survives replacing the system underneath."
        ),
        "confused_with": (
            "Not an application_component (the component is the thing that "
            "exists and can be decommissioned; the service is the promise, "
            "which can outlive it). Not an application_function (the function "
            "is internal ability, the service is the deliberately exposed "
            "subset). Not an application_interface (the interface is the "
            "access point the service is offered at). A reliable test: if the "
            "name contains a vendor or product, it is probably a component "
            "wearing a service label - rename it to the outcome the consumer "
            "buys and see whether it still makes sense."
        ),
        "properties": ["name", "description", "consumers", "service_level"],
    },
    "data_object": {
        "layer": "application",
        "definition": (
            "Data structured for automated processing, in a form an "
            "application can store, exchange or operate on."
        ),
        "use_when": (
            "Naming something that lives in a system - a 'Customer' table, an "
            "'InvoiceMessage' payload, a file layout, a document in a store. "
            "Data objects are what data lineage, GDPR records and integration "
            "contracts are drawn from."
        ),
        "confused_with": (
            "Not a business_object. A business object is a concept the "
            "BUSINESS recognises and would still recognise on paper - "
            "'Invoice', 'Contract', 'Policy'; a data object is its realisation "
            "inside software, and one business object usually has several (the "
            "invoice in the ERP, the invoice in the data warehouse, the "
            "invoice on the EDI wire). Modelling only the business object "
            "hides that duplication; modelling only the data object loses the "
            "shared meaning. If you are recording a schema, a system of "
            "record, or a copy, you want the data object. Also not an "
            "artifact: an artifact is a technology-layer FILE on a node, such "
            "as the deployed .jar or the physical database file."
        ),
        "properties": ["name", "description", "owning_component", "classification"],
    },
    # ----------------------------------------------------------------- #
    # Strategy - what the organisation can do, has, and has decided.     #
    # ----------------------------------------------------------------- #
    "capability": {
        "layer": "strategy",
        "definition": (
            "An ability that an active structure element - typically the whole "
            "organisation - possesses."
        ),
        "use_when": (
            "Naming what the organisation CAN DO, as a stable noun phrase that "
            "would still be true after a reorganisation or a system "
            "replacement - 'claims handling', 'demand forecasting', 'customer "
            "onboarding'. Capabilities are the spine of this product: "
            "applications are mapped to them, maturity is scored against them, "
            "and gaps are raised where the ability is weak. Keep systems, "
            "teams and dates out of the name."
        ),
        "confused_with": (
            "Not a resource. A capability is an ABILITY, expressed as a noun "
            "phrase ('claims handling'); a resource is an ASSET the "
            "organisation owns that enables abilities ('the claims platform', "
            "'a trained adjuster team'). Ask whether you could lose the asset "
            "and keep the ability: you can replace the claims platform and "
            "still handle claims, so the ability and the asset are different "
            "elements. Not a course_of_action either - that is what the "
            "organisation has DECIDED to do about a capability, and it has a "
            "start and an end, whereas a capability just persists. Not a "
            "value_stream: the stream is the ordered sequence that delivers "
            "value to a customer, the capability is one ability drawn on at a "
            "stage of it, so 'quote to cash' is a stream and 'pricing' is a "
            "capability used inside it. And not a business_function - a "
            "function is behaviour actually being performed in the "
            "organisation today; a capability may be one you have not built "
            "yet, which is precisely why strategy is modelled with "
            "capabilities and operations with functions."
        ),
        "properties": ["name", "description", "maturity_level", "owner"],
    },
    "course_of_action": {
        "layer": "strategy",
        "definition": (
            "An approach or plan for configuring capabilities and resources, "
            "chosen in order to achieve a goal."
        ),
        "use_when": (
            "Recording a DECISION with an owner and a horizon - 'migrate "
            "claims to the cloud by 2027', 'buy rather than build CRM', "
            "'consolidate to two ERPs'. If a leadership team could approve or "
            "reject it, it is a course of action."
        ),
        "confused_with": (
            "Not a capability (an ability you have, indefinitely) and not a "
            "resource (an asset you hold). A course of action is a choice "
            "among alternatives and could have been decided differently; the "
            "other two are facts about the organisation. Not a goal either - "
            "the goal is the end state you want ('cut run cost by a fifth'), "
            "the course of action is the route you picked to get there, and "
            "one goal usually has several candidate courses of action of which "
            "you choose one. Also not a work_package: a course of action is "
            "strategic intent, a work package is funded, scheduled work that "
            "implements it. If it has a delivery manager and a budget line, it "
            "has already become a work package."
        ),
        "properties": ["name", "description", "target_date", "realises_goal"],
    },
    "resource": {
        "layer": "strategy",
        "definition": (
            "An asset owned or controlled by an individual or organisation - "
            "human, information, financial, technological or physical."
        ),
        "use_when": (
            "Naming what the organisation HAS that its abilities depend on and "
            "that is scarce enough to argue about - 'the actuarial team', 'the "
            "customer data set', 'the transformation budget', 'the mainframe'. "
            "Resources are the right element for investment, scarcity and "
            "competitive-advantage conversations."
        ),
        "confused_with": (
            "Not a capability. This is the distinction most models get wrong. "
            "The resource is the ASSET ('a trained adjuster team'); the "
            "capability is the ABILITY it enables ('claims handling'). Test it "
            "by removal: lose the team and you can hire another one and still "
            "handle claims - the ability outlives the asset, so they are two "
            "elements, and the resource is assigned TO the capability. Also "
            "not an application_component or a node: those are the "
            "architecture's actual software and infrastructure. Model 'the "
            "claims platform' as a resource only when the point is that it is "
            "an owned, scarce, strategically significant asset; when the point "
            "is that it runs code, has interfaces and has a lifecycle, it is "
            "an application component. Both can exist for the same real-world "
            "thing at different layers, deliberately."
        ),
        "properties": ["name", "description", "resource_type", "strategic_value"],
    },
    "value_stream": {
        "layer": "strategy",
        "definition": (
            "A sequence of value-adding activities that together produce an "
            "overall result for a customer, stakeholder or end user."
        ),
        "use_when": (
            "Describing END-TO-END delivery from trigger to value received, "
            "crossing every department it needs to - 'quote to cash', 'concept "
            "to market', 'hire to retire'. Name it by its two endpoints; that "
            "phrasing is what keeps it end-to-end instead of departmental. In "
            "this product a value stream is decomposed into stages, and "
            "capabilities are mapped to those stages."
        ),
        "confused_with": (
            "Not a capability. The stream is the SEQUENCE that delivers value "
            "and is read left to right; a capability is one ABILITY drawn on "
            "at a point in it and has no sequence at all. 'Quote to cash' is a "
            "stream; 'pricing', 'credit checking' and 'invoicing' are "
            "capabilities inside it - and the same capability appears in "
            "several streams, which is exactly why they are separate elements. "
            "Not a business_process either, and this is the subtler one: a "
            "value stream is defined by the VALUE the recipient receives and "
            "stays deliberately implementation-free, whereas a business "
            "process is defined by the work performed and names steps, "
            "handoffs and performers. If your description contains a system, a "
            "team or a form, you have drifted into a business process. Model "
            "the stream for strategy and investment; model processes "
            "underneath it for operations."
        ),
        "properties": ["name", "description", "stages", "value_delivered"],
    },
    # ----------------------------------------------------------------- #
    # Implementation & Migration - how the change is delivered.          #
    # ----------------------------------------------------------------- #
    "deliverable": {
        "layer": "implementation",
        "definition": (
            "A precisely defined result produced by a work package."
        ),
        "use_when": (
            "Naming the TANGIBLE OUTPUT you can inspect and sign off - 'the "
            "migrated customer database', 'signed vendor contract', 'target "
            "architecture document', 'the deployed orders API'."
        ),
        "confused_with": (
            "Not a work_package. The work package is the EFFORT (people, "
            "budget, dates, a plan); the deliverable is the THING that effort "
            "produces and that outlives it. 'Migrate the CRM' is the work; "
            "'the migrated CRM' is the deliverable. The grammatical tell is "
            "reliable: work packages read as verbs, deliverables as nouns you "
            "could hand to someone. Ask 'what still exists when the team "
            "disbands?' - that is the deliverable. Also not a plateau: a "
            "deliverable is one artefact, a plateau is the whole state of the "
            "architecture at a moment, which several deliverables together "
            "bring about."
        ),
        "properties": ["name", "description", "produced_by", "due_date"],
    },
    "gap": {
        "layer": "implementation",
        "definition": (
            "A statement of difference between two plateaus - what the "
            "baseline has or lacks compared with the target."
        ),
        "use_when": (
            "Recording a specific, named shortfall between where the "
            "architecture is and where it must be - 'no single customer "
            "identifier across the three CRMs', 'settlement cannot run "
            "intraday'. A gap is the justification for a work package: every "
            "piece of funded change should trace back to one."
        ),
        "confused_with": (
            "Not a plateau. The plateau is a STATE; the gap is the DIFFERENCE "
            "between two states, so a gap always presupposes a baseline and a "
            "target and is meaningless without them - if you cannot name both, "
            "you are not ready to record a gap. Not a work_package either: the "
            "gap is the problem, the work package is the response, and keeping "
            "them apart is what lets you show that a proposed programme leaves "
            "some gaps unaddressed - collapse them and that check becomes "
            "impossible. Also not an assessment: an assessment is a finding "
            "about the CURRENT estate on its own terms; a gap is relative to a "
            "declared target architecture. Phrase gaps as a missing or excess "
            "property, never as a solution - 'no shared customer key' is a "
            "gap, 'implement an MDM hub' is a work package."
        ),
        "properties": ["name", "description", "baseline_plateau",
                       "target_plateau", "severity"],
    },
    "implementation_event": {
        "layer": "implementation",
        "definition": (
            "A behaviour element in the implementation and migration domain "
            "that denotes a state change; it happens at a point in time."
        ),
        "use_when": (
            "Marking a dated moment in the delivery of change - 'go-live', "
            "'contract signed', 'phase 1 gate passed', 'legacy system switched "
            "off'. These are the milestones a roadmap hangs dates on."
        ),
        "confused_with": (
            "Not a work_package: the work package spans time and consumes "
            "budget, the event is instantaneous and consumes neither. Not a "
            "plateau either, which is the subtler confusion - an event is the "
            "MOMENT the architecture changes, the plateau is the STATE it is "
            "in afterwards and remains in. 'Go-live' is an event; 'live on the "
            "new platform' is the plateau it opens. Also not an "
            "application_event: that is a runtime state change inside "
            "software; this one is about the programme delivering the change."
        ),
        "properties": ["name", "description", "event_date"],
    },
    "plateau": {
        "layer": "implementation",
        "definition": (
            "A relatively stable state of the architecture that exists during "
            "a limited period of time."
        ),
        "use_when": (
            "Naming a coherent STATE the estate rests in - the baseline "
            "('today'), the target ('2028 target state'), and each transition "
            "state in between ('after CRM consolidation, before ERP "
            "migration'). Plateaus are how a roadmap is expressed in "
            "ArchiMate: the roadmap is the ordered series of plateaus, the "
            "gaps between them, and the work packages that close those gaps. A "
            "plateau must be self-consistent - everything in it can run "
            "together - because that is what makes it a place you could stop "
            "if the funding did."
        ),
        "confused_with": (
            "Not a work_package, and not a phase of a project. A plateau "
            "describes the architecture at REST between changes; a work "
            "package is the change itself. 'Q3' is not a plateau, and neither "
            "is 'migration phase 2' - those are units of work, and naming them "
            "as plateaus is the single commonest reason a generated roadmap is "
            "untrue. Not a gap either: the plateau is a state, the gap is the "
            "difference between two of them. The test for a genuine plateau: "
            "could the organisation stop here and operate for a year? If not, "
            "it is a moment inside a work package, not a plateau."
        ),
        "properties": ["name", "description", "target_date", "sequence_order"],
    },
    "work_package": {
        "layer": "implementation",
        "definition": (
            "A series of actions identified and designed to achieve specific "
            "results within specified time and resource constraints."
        ),
        "use_when": (
            "Naming FUNDED, SCHEDULED work with an owner - a project, a "
            "programme, a workstream, a discrete slice of change. If it has "
            "dates, a budget and someone accountable for finishing it, it is a "
            "work package."
        ),
        "confused_with": (
            "Not a deliverable - the work package is the effort, the "
            "deliverable is what it produces and leaves behind. Not a "
            "course_of_action: that is the strategic choice ('move claims to "
            "the cloud'), this is the funded execution of it ('claims cloud "
            "migration, FY27 Q1-Q3'); one course of action typically becomes "
            "several work packages. Not a business_process either, which "
            "catches people out - a process is repeatable operational "
            "behaviour that runs indefinitely, a work package is one-off "
            "change work that finishes. 'Handle a claim' recurs forever and is "
            "a process; 'replace the claims system' happens once and is a work "
            "package."
        ),
        "properties": ["name", "description", "start_date", "end_date",
                       "estimated_cost", "owner"],
    },
}


def specs_for_layer(layer: str) -> Dict[str, dict]:
    """Every element type declared for one ArchiMate layer."""
    return {k: v for k, v in ELEMENT_SPECS.items() if v.get("layer") == layer}


def tool_description(element_type: str, spec: dict) -> str:
    """The text the model reads when deciding whether to call this tool.

    Assembled rather than hand-written per tool so that every element carries
    the same three pieces of guidance, and so that adding an element type
    cannot accidentally ship a tool whose description omits the distinction
    that stops it being misused.
    """
    readable = element_type.replace("_", " ")
    # "an application service", not "a application service". A tool description
    # is the first thing a user sees of the assistant's competence, and reading
    # like a mail-merge undermines advice that is otherwise expert.
    article = "an" if readable[:1].lower() in "aeiou" else "a"
    return (
        "Create %s %s (ArchiMate 3.2 %s layer). %s USE WHEN: %s IMPORTANT: %s"
        % (
            article,
            readable,
            spec["layer"],
            spec["definition"],
            spec["use_when"],
            spec["confused_with"],
        )
    )
