create table phone_location (
    at         timestamptz primary key,
    lat        double precision not null,
    lon        double precision not null,
    accuracy_m real
);
