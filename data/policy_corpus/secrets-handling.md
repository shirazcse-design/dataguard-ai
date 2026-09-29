---
policy_id: POL-SEC
title: Credentials and Secrets Handling Standard
version: "1.1"
effective_date: "2025-08-01"
status: current
supersedes: null
policy_owner: Head of Platform Security
source: synthetic/harbourline/secrets-standard
---

# Credentials and Secrets Handling Standard

## 1. Purpose

This standard covers passwords, API keys, tokens, certificates and other secrets used to access
Harbourline systems.

## 2. Storage

Secrets must be stored in the company secrets vault. Secrets must never be stored in source code,
in configuration files committed to a repository, in tickets, in chat messages or in email.

## 3. Sharing

Passwords must never be shared. Service credentials are shared only by granting access through the
secrets vault's access policies, never by sending the secret itself.

## 4. Rotation

API keys and service credentials are rotated at least every 90 days, and immediately if exposure is
suspected.

## 5. Exposed secrets

If a secret is exposed, for example committed to a repository or pasted into a chat, it must be
revoked or rotated immediately and reported as a security incident under the Incident Response
Policy.

## 6. Passwords

User passwords must be at least 14 characters long, must be stored only in the company-approved
password manager, and must be combined with multi-factor authentication.
