# Host Application Audit & Architecture: Chatwoot

## 1. Candidate Evaluation & License Audit
- **Host System**: Chatwoot (Community Edition)
- **Upstream Repository**: [https://github.com/chatwoot/chatwoot](https://github.com/chatwoot/chatwoot)
- **License**: GNU Affero General Public License v3.0 (`AGPL-3.0`)
- **Audit Date**: 2026-10-14
- **Auditor**: Relay Engineering Team

### Section 13 Decision & Isolation Strategy
Under AGPL-3.0 Section 13 (Remote Network Interaction), if a modified host software is operated across a network, the modified source must be made available to all users interacting with it remotely.

**Formal Strategy**:
1. **Network Boundary Isolation**: Relay operates as an entirely independent external process. Relay interacts with Chatwoot exclusively via Chatwoot's standard HTTP Webhook dispatch and REST API (`POST /api/v1/accounts/{id}/conversations/{id}/messages` and `POST /api/v1/accounts/{id}/conversations/{id}/labels`).
2. **Minimal In-Host Hook**: For Phase 5 shadow triage, any in-host trigger is implemented as a self-contained feature-flagged module of under 30 lines of code. This hook is kept in an isolated local test harness with shadow comparison tables.
3. **No Code Contamination**: Relay's core codebase does not import or link against any Chatwoot GPL/AGPL libraries.

---

## 2. Documented Single Request Path

```
Customer Ticket Submitted
         │
         ▼
[ Chatwoot Webhook Event ]  -->  `conversation_created` / `message_created`
         │
         ▼
[ Relay Ingestion Endpoint ]  -->  Creates / Enqueues `runs` row in Postgres
         │
         ▼
[ Relay Worker ]  -->  Acquires lease with fencing token (`SELECT FOR UPDATE SKIP LOCKED`)
         │
         ├── Policy query?  -->  Calls Clearance (Project 1) `retrieve`
         ├── Invoice attached? --> Calls Ledgerline (Project 2) `extract`
         │
         ▼
[ Approval Gate ]  -->  Draft reply generated, `runs.status = waiting_approval`
         │              Worker releases lease and terminates (Zero-Cost Suspension)
         ▼
[ Human Approver ]  -->  Approves via Web / Callback
         │
         ▼
[ Relay Resumption ]  -->  Reclaims run, stamps effect via External Sink (`sink_service.py`)
         │
         ▼
[ Chatwoot REST API ]  -->  `POST /api/v1/accounts/1/conversations/42/messages` (as private draft or reply)
```

---

## 3. Data Model Mapping

```
┌─────────────────────────────────┐           ┌─────────────────────────────────┐
│     Chatwoot Data Model         │           │        Relay Data Model         │
├─────────────────────────────────┤           ├─────────────────────────────────┤
│ accounts (id, name)             │ ────────> │ tenant_id / quota (tenant_id)   │
│ contacts (id, name, email)      │           │                                 │
│ conversations (id, status, ...) │ ────────> │ runs (id, status, lease_epoch)  │
│ messages (id, content, sender)  │ ────────> │ run_events (seq, payload)       │
│ labels (id, title)              │ <──────── │ effects (tool_name='act_label') │
│ replies (outgoing content)      │ <──────── │ effects (tool_name='act_reply') │
└─────────────────────────────────┘           └─────────────────────────────────┘
```

---

## 4. Local Execution & Corrections
Chatwoot can be run locally via standard Docker Compose (`docker compose -f docker-compose.chatwoot.yml up`).
- **Correction noted in upstream README**: Newer versions of Chatwoot require `SECRET_KEY_BASE` and `FRONTEND_URL` strictly defined in `.env` before database migrations (`bundle exec rake db:chatwoot_prepare`) will run cleanly without throwing an unhandled uninitialized constant error.
- **Webhook Target**: Pointed to `http://host.docker.internal:8000/webhooks/chatwoot` or local Relay ingress.
