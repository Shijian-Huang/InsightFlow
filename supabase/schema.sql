create table if not exists public.projects (
  project_id text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists projects_user_updated_at_idx
  on public.projects (user_id, updated_at desc);

create table if not exists public.analyses (
  analysis_id text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id text references public.projects(project_id) on delete cascade,
  filename text,
  paper_title text,
  summary_mode text,
  status text not null default 'completed',
  processing_seconds numeric,
  summary text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  record jsonb not null
);

alter table public.analyses
  add column if not exists project_id text references public.projects(project_id) on delete cascade;

alter table public.analyses
  add column if not exists status text not null default 'completed';

alter table public.analyses
  add column if not exists updated_at timestamptz not null default now();

create index if not exists analyses_user_created_at_idx
  on public.analyses (user_id, created_at desc);

create index if not exists analyses_user_project_created_at_idx
  on public.analyses (user_id, project_id, created_at desc);

alter table public.projects enable row level security;

alter table public.analyses enable row level security;

drop policy if exists "Users can read own projects" on public.projects;
create policy "Users can read own projects"
  on public.projects
  for select
  using ((select auth.uid()) = user_id);

drop policy if exists "Users can insert own projects" on public.projects;
create policy "Users can insert own projects"
  on public.projects
  for insert
  with check ((select auth.uid()) = user_id);

drop policy if exists "Users can update own projects" on public.projects;
create policy "Users can update own projects"
  on public.projects
  for update
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "Users can delete own projects" on public.projects;
create policy "Users can delete own projects"
  on public.projects
  for delete
  using ((select auth.uid()) = user_id);

drop policy if exists "Users can read own analyses" on public.analyses;
create policy "Users can read own analyses"
  on public.analyses
  for select
  using ((select auth.uid()) = user_id);

drop policy if exists "Users can insert own analyses" on public.analyses;
create policy "Users can insert own analyses"
  on public.analyses
  for insert
  with check ((select auth.uid()) = user_id);

drop policy if exists "Users can update own analyses" on public.analyses;
create policy "Users can update own analyses"
  on public.analyses
  for update
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists "Users can delete own analyses" on public.analyses;
create policy "Users can delete own analyses"
  on public.analyses
  for delete
  using ((select auth.uid()) = user_id);

grant select, insert, update, delete on public.projects to authenticated;
grant select, insert, update, delete on public.analyses to authenticated;
