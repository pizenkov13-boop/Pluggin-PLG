-- Payment-record retention after account deletion (landing/privacy.html §2).
--
-- The privacy policy keeps payment data for 5 years after the contract ends
-- (account deletion), then destroys it. delete_user_account() nulls user_id,
-- which used to erase the only trace of *when* the account was deleted, so
-- the 5-year clock had nothing to start from. anonymized_at is that clock;
-- purge_payment_events() (cloud/app/legal.py) deletes rows once it and the
-- payment's own reporting year are both past PLG_PAYMENT_LOG_RETENTION_DAYS.
--
-- Rows of active accounts keep anonymized_at NULL and are never purged: the
-- contract is still running.

alter table public.payment_events
  add column if not exists anonymized_at timestamptz;

create index if not exists payment_events_anonymized_at_idx
  on public.payment_events (anonymized_at)
  where anonymized_at is not null;

-- Accounts deleted before this migration: user_id is already NULL but the
-- deletion date was never recorded. Start their clock now (keeps the rows
-- longer, never shorter) and strip the user UUID the webhook body still
-- carries in object.metadata.user_id.
update public.payment_events
set anonymized_at = now(),
    payload = payload #- '{object,metadata,user_id}'
where user_id is null
  and anonymized_at is null;
