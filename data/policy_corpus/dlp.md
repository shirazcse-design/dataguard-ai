---
policy_id: POL-DLP
title: Data Loss Prevention Policy
version: "2.1"
effective_date: "2025-06-01"
status: current
supersedes: null
policy_owner: Head of Data Protection
source: synthetic/harbourline/dlp-policy
---

# Data Loss Prevention Policy

## 1. Purpose

This policy sets out the controls Harbourline uses to prevent the unauthorised movement of
sensitive information outside the company, and the activities that are prohibited.

## 2. Scope

This policy applies to all employees and contractors and to every channel through which data can
leave the company: email, web uploads, cloud storage, removable media, collaboration tools and
generative AI tools.

## 3. Monitored channels

### 3.1 Email

Outbound email to external domains is scanned for Confidential and Restricted information. Email
containing Restricted information to an external recipient is blocked unless it is encrypted and
the data owner has approved the transfer.

### 3.2 Web uploads and cloud storage

Uploads to websites and cloud services are inspected by the secure web gateway. Uploads to
unsanctioned cloud services generate a DLP alert for review by the Data Protection team.

### 3.3 Removable media

Writing to USB drives and other removable media is blocked by default on company devices. An
exception requires a service ticket approved by the employee's line manager and by Information
Security.

## 4. Prohibited activities

### 4.1 Personal email

Sending Confidential or Restricted information to a personal email account, including forwarding
it to yourself, is prohibited.

### 4.2 Personal cloud storage

Uploading company information classified Internal or above to a personal cloud-storage account,
such as a personal Dropbox, personal Google Drive or personal OneDrive, is prohibited.

### 4.3 Source code

Uploading company source code to a public repository, a personal repository account or a public
paste site is prohibited unless the Open Source Program Office has approved the release.

### 4.4 Generative AI tools

Pasting Confidential or Restricted information into public generative AI tools is prohibited. Only
the company-approved AI tools listed in the AI Tools Register may be used with company information.

## 5. Exceptions

A business need that conflicts with this policy requires a DLP exception request. Exceptions must be
approved by the data owner and the Head of Data Protection, and are granted for a maximum of 90
days.

## 6. Enforcement

DLP alerts are triaged by the Data Protection team within one business day. Violations of this
policy may result in disciplinary action, up to and including termination of employment.
