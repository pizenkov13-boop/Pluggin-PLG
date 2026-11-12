-- 008_atomic_quota.sql
-- Race-free beat consumption.
--
-- The old /v1/generate flow read beats_used, checked the limit, ran the
-- (expensive) LLM, then wrote beats_used+1. Concurrent requests on one account
-- could each pass the check and over-generate / over-spend, and the final write
-- was last-writer-wins (lost increments). These functions move the roll + check
-- + increment into a single row-locked transaction so it is atomic.
--
-- The server reserves a credit via plg_consume_beat BEFORE calling the LLM, and
-- refunds via plg_refund_beat if generation fails. Idempotent to re-run.

create or replace function plg_consume_beat(
  p_user uuid,
  p_beat_limit int,
  p_daily_limit int,
  p_trial_beats int,
  p_period_days int
) returns jsonb
language plpgsql
as $$
declare
  v_status text;
  v_period_start timestamptz;
  v_daily_reset date;
  v_beats_used int;
  v_beats_today int;
  v_trial_used int;
  v_grace_until timestamptz;
  v_today date := (now() at time zone 'utc')::date;
  v_consumed_trial boolean := false;
begin
  -- Row lock serialises concurrent consumes for this user.
  select status, period_start, daily_reset, beats_used, beats_today,
         trial_beats_used, grace_until
    into v_status, v_period_start, v_daily_reset, v_beats_used, v_beats_today,
         v_trial_used, v_grace_until
  from public.profiles
  where id = p_user
  for update;

  if not found then
    return jsonb_build_object('allowed', false, 'reason', 'no_profile');
  end if;

  v_status := coalesce(v_status, 'expired');
  v_period_start := coalesce(v_period_start, now());
  v_daily_reset := coalesce(v_daily_reset, v_today);
  v_beats_used := coalesce(v_beats_used, 0);
  v_beats_today := coalesce(v_beats_today, 0);
  v_trial_used := coalesce(v_trial_used, 0);

  -- Grace expiry.
  if v_status = 'grace' and v_grace_until is not null and now() > v_grace_until then
    v_status := 'expired';
    v_grace_until := null;
  end if;

  -- Daily reset (applies to every status).
  if v_today > v_daily_reset then
    v_beats_today := 0;
    v_daily_reset := v_today;
  end if;

  -- Monthly period roll (paid statuses only), mirrors roll_profile().
  if v_status in ('active', 'grace') then
    while now() >= v_period_start + make_interval(days => p_period_days) loop
      v_period_start := v_period_start + make_interval(days => p_period_days);
      v_beats_used := 0;
    end loop;
  end if;

  -- Daily cap is disabled when p_daily_limit is 0.
  if p_daily_limit > 0 and v_beats_today >= p_daily_limit then
    update public.profiles set
      status=v_status, period_start=v_period_start, daily_reset=v_daily_reset,
      beats_used=v_beats_used, beats_today=v_beats_today, grace_until=v_grace_until,
      updated_at=now()
    where id=p_user;
    return jsonb_build_object('allowed', false, 'reason', 'daily', 'status', v_status);
  end if;

  if v_status = 'trial' then
    if v_trial_used >= p_trial_beats then
      update public.profiles set status='expired', updated_at=now() where id=p_user;
      return jsonb_build_object('allowed', false, 'reason', 'trial_ended', 'status', 'expired');
    end if;
    v_trial_used := v_trial_used + 1;
    v_beats_today := v_beats_today + 1;
    v_consumed_trial := true;
    if v_trial_used >= p_trial_beats then
      v_status := 'expired';
    end if;
  elsif v_status in ('active', 'grace') then
    if v_beats_used >= p_beat_limit then
      update public.profiles set
        status=v_status, period_start=v_period_start, daily_reset=v_daily_reset,
        beats_used=v_beats_used, beats_today=v_beats_today, grace_until=v_grace_until,
        updated_at=now()
      where id=p_user;
      return jsonb_build_object('allowed', false, 'reason', 'monthly', 'status', v_status);
    end if;
    v_beats_used := v_beats_used + 1;
    v_beats_today := v_beats_today + 1;
  else
    return jsonb_build_object('allowed', false, 'reason', 'inactive', 'status', v_status);
  end if;

  update public.profiles set
    status = v_status,
    period_start = v_period_start,
    daily_reset = v_daily_reset,
    beats_used = v_beats_used,
    beats_today = v_beats_today,
    trial_beats_used = v_trial_used,
    grace_until = v_grace_until,
    updated_at = now()
  where id = p_user;

  return jsonb_build_object(
    'allowed', true,
    'status', v_status,
    'consumed_trial', v_consumed_trial,
    'beats_used', v_beats_used,
    'beats_today', v_beats_today,
    'trial_beats_used', v_trial_used
  );
end;
$$;

create or replace function plg_refund_beat(p_user uuid, p_was_trial boolean)
returns void
language plpgsql
as $$
begin
  if p_was_trial then
    update public.profiles set
      trial_beats_used = greatest(0, coalesce(trial_beats_used, 0) - 1),
      beats_today = greatest(0, coalesce(beats_today, 0) - 1),
      updated_at = now()
    where id = p_user;
  else
    update public.profiles set
      beats_used = greatest(0, coalesce(beats_used, 0) - 1),
      beats_today = greatest(0, coalesce(beats_today, 0) - 1),
      updated_at = now()
    where id = p_user;
  end if;
end;
$$;

-- Server-only: these are called with the service role. Clients must never
-- reach them directly via PostgREST.
revoke all on function plg_consume_beat(uuid, int, int, int, int) from public, anon, authenticated;
revoke all on function plg_refund_beat(uuid, boolean) from public, anon, authenticated;
grant execute on function plg_consume_beat(uuid, int, int, int, int) to service_role;
grant execute on function plg_refund_beat(uuid, boolean) to service_role;
