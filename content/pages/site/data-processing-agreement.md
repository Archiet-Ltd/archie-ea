---
page_family: site
title: "Data processing agreement"
description: "How Archiet Ltd handles personal data and organisation content in the hosted Entelim service, in plain words."
page_role: "A plain statement of practice for data processed in the hosted service."
---

# Data processing agreement

Last updated: 19 October 2026.

This page says how Archiet Ltd handles data when it runs Entelim for your organisation. It forms part
of the [terms](/terms) that apply to the hosted service. If you run Entelim yourself, your data stays
on your own infrastructure and this page does not apply.

## Who is responsible for what

Your organisation is the controller of the content its users put into Entelim and of the personal data
in it. Archiet Ltd is the processor: it handles that data to run the service for you and for no other
purpose.

For the details of the people who administer your account, such as their name and work email address,
Archiet Ltd decides how the data is used. The [privacy page](/privacy) covers that.

## What is processed

- **Organisation content.** The records your users create or import: applications, risks, roadmaps,
  documents and the rest of what Entelim covers, including any personal data your users choose to
  record, such as the names of application owners or supplier contacts.
- **User accounts.** Name, work email address, role, a salted hash of the password, and a record of
  sign-in and significant changes.
- **Assistant conversations.** The questions people ask the assistant and its answers, kept with the
  user who asked.

## Why it is processed

To store, organise, display, analyse and export your records; to answer your users' questions; to sign
users in and control what they can reach; and to send account emails such as password resets and
invitations. Archiet Ltd does not use your organisation content for any other purpose.

## How the data is protected

- Traffic between your users and the service is encrypted with TLS.
- Each organisation's records are kept apart. The application restricts every query to the signed-in
  organisation, and tenant isolation is enforced in the database itself with row-level security.
- Model provider keys and saved connector credentials are stored encrypted.
- Passwords are stored as salted hashes, never in readable form.
- Access inside a workspace is controlled by roles that your administrators assign.
- Significant changes are written to an audit log that is append-only.
- Changes proposed by the assistant wait for a person's approval before they are written.

## Exporting and deleting data

- **Export.** Applications can be exported as CSV or JSON, and ArchiMate models can be exported as
  files. Any user can export their own personal data.
- **Requests from the people in your data.** Authorised administrators can record a data subject request in the
  workspace, trace where a person's data sits, export what is held about that person, and carry out
  an erasure. An erasure removes the person's account details and sign-in credentials, their assistant
  conversations and the documents they uploaded to the assistant, ends their open sessions, and writes
  an audit entry.
- **Closing an organisation.** To close your organisation and have its data removed, send an enquiry
  through the [contact form](/contact).

## Sub-processors

The outside services that handle your data are listed on the [sub-processors page](/sub-processors),
which also says where the assistant sends your content.

## Questions

Use the [contact form](/contact) to ask about this page or to raise a request about your data.
