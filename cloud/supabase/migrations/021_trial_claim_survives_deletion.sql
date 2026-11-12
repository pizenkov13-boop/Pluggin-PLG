-- Keep the device trial claim after the account that made it is deleted.
--
-- device_trial_claims.first_user_id referenced auth.users ON DELETE CASCADE, so
-- delete_user_account() -> auth.admin.delete_user() wiped the claim together
-- with the account. Delete the account, sign up again on the same PC, and the
-- device looked untouched: a fresh trial every time.
--
-- The claim now outlives the account: first_user_id becomes nullable and is set
-- to NULL on delete. claim_device_trial() (cloud/app/security.py) already treats
-- a claim owned by nobody as "trial used" until PLG_TRIAL_CLAIM_TTL_DAYS passes.
--
-- Claims that were cascaded away before this migration cannot be recovered.

alter table public.device_trial_claims
  alter column first_user_id drop not null;

alter table public.device_trial_claims
  drop constraint if exists device_trial_claims_first_user_id_fkey;

alter table public.device_trial_claims
  add constraint device_trial_claims_first_user_id_fkey
  foreign key (first_user_id) references auth.users (id) on delete set null;
