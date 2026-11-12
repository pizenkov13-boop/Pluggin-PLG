-- Privacy hardening: prompt previews are no longer part of the data contract.
-- Keep the column for schema compatibility, but erase historical previews.
update public.beats set prompt_preview = null where prompt_preview is not null;

-- The Russian self-hosted deployment is represented by the existing `cis`
-- region code used by the schema. Do not label new profiles as EU.
alter table public.profiles
  alter column data_region set default 'cis';

update public.profiles
set data_region = 'cis'
where data_region = 'eu';
