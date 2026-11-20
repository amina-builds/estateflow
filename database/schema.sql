-- EstateFlow AI database schema (Supabase / PostgreSQL)
-- Run ONCE: Supabase dashboard > SQL Editor > New query > paste all of this > Run.

create sequence if not exists leads_seq        start 1001;
create sequence if not exists properties_seq   start 2001;
create sequence if not exists appointments_seq start 3001;
create sequence if not exists followups_seq    start 4001;

create table if not exists public.leads (
  id                  text primary key default ('L-' || nextval('leads_seq')),
  seq                 bigint generated always as identity,
  name                text not null,
  phone               text not null,
  email               text default '',
  source              text default '',
  interested_property text default '',
  budget              numeric default 0 check (budget >= 0),
  preferred_location  text default '',
  status              text not null default 'New'
    check (status in ('New','Contacted','Qualified','Interested','Negotiation','Converted','Lost')),
  assigned_agent      text default 'Unassigned',
  archived            boolean not null default false,
  created_at          timestamptz not null default now()
);

create table if not exists public.properties (
  id              text primary key default ('P-' || nextval('properties_seq')),
  seq             bigint generated always as identity,
  title           text not null,
  property_type   text not null default 'House'
    check (property_type in ('Residential','Commercial','Apartment','House','Plot','Office','Shop')),
  location        text not null,
  price           numeric not null check (price >= 0),
  bedrooms        integer default 0 check (bedrooms >= 0),
  bathrooms       integer default 0 check (bathrooms >= 0),
  area            text default '',
  status          text not null default 'Available'
    check (status in ('Available','Reserved','Sold','Rented','Archived')),
  assigned_agent  text default 'Unassigned',
  created_at      timestamptz not null default now()
);

create table if not exists public.appointments (
  id               text primary key default ('A-' || nextval('appointments_seq')),
  seq              bigint generated always as identity,
  client_name      text not null,
  property_title   text default '',
  assigned_agent   text default 'Unassigned',
  appointment_date date not null,
  start_time       time not null,
  appointment_type text default 'Viewing',
  status           text not null default 'Scheduled'
    check (status in ('Scheduled','Confirmed','Completed','Cancelled','Rescheduled')),
  created_at       timestamptz not null default now()
);

create table if not exists public.followups (
  id              text primary key default ('F-' || nextval('followups_seq')),
  seq             bigint generated always as identity,
  task            text not null,
  lead_name       text default '',
  assigned_agent  text default 'Unassigned',
  due_date        date not null,
  priority        text not null default 'Medium' check (priority in ('Low','Medium','High','Urgent')),
  status          text not null default 'Pending'
    check (status in ('Pending','Completed','Overdue','Rescheduled')),
  created_at      timestamptz not null default now()
);

create table if not exists public.activity (
  id          bigint generated always as identity primary key,
  type        text not null,
  description text default '',
  user_name   text default '',
  created_at  timestamptz not null default now()
);

-- SECURITY: lock every table. With RLS on and no policies, the public (anon) key can read nothing.
-- Only the backend, using the secret/service key, can read and write.
alter table public.leads        enable row level security;
alter table public.properties   enable row level security;
alter table public.appointments enable row level security;
alter table public.followups    enable row level security;
alter table public.activity     enable row level security;

revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
