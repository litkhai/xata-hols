-- A small app schema with personal data in it, used by the pgroll, agent fan-out and
-- anonymised-clone labs. Every value is generated; no row describes a real person.
create table users (
  id         bigint primary key,
  email      text not null unique,
  full_name  text not null,
  phone      text not null,
  created_at timestamptz not null default now()
);
insert into users
select g,
       'user' || g || '@example.com',
       (array['Kim','Lee','Park','Choi','Jung'])[1 + g % 5] || ' ' || (array['Minjun','Seoyeon','Jiho','Hayun','Doyun'])[1 + (g / 5) % 5],
       '010-' || lpad((g % 10000)::text, 4, '0') || '-' || lpad(((g * 7) % 10000)::text, 4, '0'),
       now() - (g % 365) * interval '1 day'
from generate_series(1, 100000) g;

create table orders (
  id         bigint primary key,
  user_id    bigint not null references users(id),
  amount     numeric(12,2) not null,
  created_at timestamptz not null default now()
);
insert into orders
select g, 1 + (g % 100000), round((random() * 500)::numeric, 2), now() - (g % 720) * interval '1 hour'
from generate_series(1, 300000) g;
analyze;
