-- CE BetterTrader: persistencia gratuita en Supabase
-- Ejecuta este script en Supabase > SQL Editor.
create table if not exists public.app_store (
  store_key text primary key,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- Solo el backend usa la service_role key, que evita estas políticas RLS.
-- No expongas esa clave en el frontend ni en GitHub.
alter table public.app_store enable row level security;

create or replace function public.touch_app_store_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists app_store_updated_at on public.app_store;
create trigger app_store_updated_at
before update on public.app_store
for each row execute procedure public.touch_app_store_updated_at();
