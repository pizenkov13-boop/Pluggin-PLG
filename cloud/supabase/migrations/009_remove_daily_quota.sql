-- 009_remove_daily_quota.sql
-- PLG sells 30 generations for 30 days. There is no daily generation cap.
-- Keep the existing RPC signature so deployed API versions remain compatible.

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

  if v_status = 'grace' and v_grace_until is not null and now() > v_grace_until then
    v_status := 'expired';
    v_grace_until := null;
  end if;

  -- Retained only as an activity statistic, never as an access limit.
  if v_today > v_daily_reset then
    v_beats_today := 0;
    v_daily_reset := v_today;
  end if;

  if v_status in ('active', 'grace') then
    while now() >= v_period_start + make_interval(days => p_period_days) loop
      v_period_start := v_period_start + make_interval(days => p_period_days);
      v_beats_used := 0;
    end loop;
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
