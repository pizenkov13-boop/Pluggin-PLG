-- One short-lived, hashed verification code per account.
create table if not exists public.email_verification_codes (
  user_id uuid primary key references auth.users (id) on delete cascade,
  code_hash text not null,
  expires_at timestamptz not null,
  attempts int not null default 0 check (attempts >= 0),
  last_sent_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

alter table public.email_verification_codes enable row level security;
