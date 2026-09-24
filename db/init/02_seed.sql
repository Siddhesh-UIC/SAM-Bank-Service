-- Dummy data. All names, numbers and PINs are fictional test data.

INSERT INTO customers (cif, full_name, phone, email, date_of_birth, city, pin_hash) VALUES
('CIF0001', 'Budi Santoso',        '+6281234567801', 'budi.santoso@contoh.id',   '1985-03-12', 'Jakarta',    crypt('123456', gen_salt('bf'))),
('CIF0002', 'Siti Nurhaliza',      '+6281234567802', 'siti.n@contoh.id',         '1990-07-25', 'Bandung',    crypt('234567', gen_salt('bf'))),
('CIF0003', 'Agus Wijaya',         '+6281234567803', 'agus.wijaya@contoh.id',    '1978-11-02', 'Surabaya',   crypt('345678', gen_salt('bf'))),
('CIF0004', 'Dewi Lestari',        '+6281234567804', 'dewi.lestari@contoh.id',   '1995-01-30', 'Yogyakarta', crypt('456789', gen_salt('bf'))),
('CIF0005', 'Rizky Pratama',       '+6281234567805', 'rizky.p@contoh.id',        '2000-05-17', 'Medan',      crypt('567890', gen_salt('bf'))),
('CIF0006', 'Putri Ayu Maharani',  '+6281234567806', 'putri.ayu@contoh.id',      '1988-09-09', 'Denpasar',   crypt('678901', gen_salt('bf'))),
('CIF0007', 'Hendra Gunawan',      '+6281234567807', 'hendra.g@contoh.id',       '1972-12-21', 'Semarang',   crypt('789012', gen_salt('bf'))),
('CIF0008', 'Rina Kartika Sari',   '+6281234567808', 'rina.kartika@contoh.id',   '1993-04-04', 'Makassar',   crypt('890123', gen_salt('bf')));

-- balance is the opening balance here; recent transactions are added on top below.
INSERT INTO accounts (account_no, customer_id, product, balance, status, opened_at) VALUES
('1230000001', 1, 'TABUNGAN', 15250000.00, 'ACTIVE',  '2015-06-01'),
('1230000002', 1, 'GIRO',     87500000.00, 'ACTIVE',  '2018-02-14'),
('1230000003', 2, 'TABUNGAN',  4750000.00, 'ACTIVE',  '2019-08-20'),
('1230000004', 3, 'TABUNGAN', 32100000.00, 'ACTIVE',  '2010-01-05'),
('1230000005', 3, 'DEPOSITO',250000000.00, 'ACTIVE',  '2022-03-01'),
('1230000006', 4, 'TABUNGAN',  2300000.00, 'ACTIVE',  '2021-11-11'),
('1230000007', 5, 'TABUNGAN',   850000.00, 'ACTIVE',  '2023-07-07'),
('1230000008', 6, 'TABUNGAN', 12600000.00, 'ACTIVE',  '2016-04-18'),
('1230000009', 7, 'TABUNGAN', 45800000.00, 'ACTIVE',  '2008-09-30'),
('1230000010', 7, 'GIRO',    120000000.00, 'DORMANT', '2012-12-12'),
('1230000011', 8, 'TABUNGAN',  6900000.00, 'ACTIVE',  '2020-10-10');

INSERT INTO cards (customer_id, account_id, card_type, network, card_last4, expiry, status, blocked_at, block_reason) VALUES
(1, 1,    'DEBIT',  'GPN',        '4821', '08/28', 'ACTIVE',  NULL, NULL),
(1, NULL, 'CREDIT', 'VISA',       '9034', '11/27', 'ACTIVE',  NULL, NULL),
(2, 3,    'DEBIT',  'GPN',        '1177', '02/29', 'ACTIVE',  NULL, NULL),
(3, 4,    'DEBIT',  'MASTERCARD', '5520', '06/27', 'ACTIVE',  NULL, NULL),
(3, NULL, 'CREDIT', 'MASTERCARD', '7713', '09/28', 'ACTIVE',  NULL, NULL),
(4, 6,    'DEBIT',  'GPN',        '3306', '12/28', 'ACTIVE',  NULL, NULL),
(5, 7,    'DEBIT',  'GPN',        '6642', '03/30', 'BLOCKED', now() - interval '20 days', 'LOST'),
(5, 7,    'DEBIT',  'GPN',        '6650', '03/30', 'ACTIVE',  NULL, NULL),
(6, 8,    'DEBIT',  'VISA',       '2289', '07/29', 'ACTIVE',  NULL, NULL),
(7, 9,    'DEBIT',  'GPN',        '8015', '05/27', 'ACTIVE',  NULL, NULL),
(8, 11,   'DEBIT',  'GPN',        '4408', '10/28', 'ACTIVE',  NULL, NULL);

-- ~25 transactions per active account over the last 30 days, deterministic via setseed.
SELECT setseed(0.42);
WITH templates(k, description, channel, min_amt, max_amt) AS (SELECT row_number() OVER (), * FROM (VALUES
    ('Tarik tunai ATM',               'ATM',      -2500000, -100000),
    ('Pembayaran QRIS - Indomaret',   'QRIS',       -350000,  -15000),
    ('Pembayaran QRIS - Kopi Kenangan','QRIS',       -85000,  -25000),
    ('Transfer ke BCA',               'TRANSFER', -5000000, -100000),
    ('Transfer masuk',                'TRANSFER',   100000, 5000000),
    ('Pembelian pulsa Telkomsel',     'MOBILE',     -200000,  -25000),
    ('Pembayaran listrik PLN',        'MOBILE',     -750000, -150000),
    ('Belanja Tokopedia',             'EDC',       -1500000,  -50000),
    ('Pembayaran BPJS Kesehatan',     'MOBILE',     -150000, -150000),
    ('Setoran tunai',                 'TELLER',     500000, 10000000)
) v), picks AS (
    SELECT a.id AS account_id,
           1 + floor(random() * 10)::int AS k,
           now() - (random() * interval '30 days') AS posted_at,
           random() AS r
    FROM accounts a
    CROSS JOIN generate_series(1, 25)
    WHERE a.status = 'ACTIVE' AND a.product <> 'DEPOSITO'
)
INSERT INTO transactions (account_id, posted_at, description, channel, amount)
SELECT p.account_id, p.posted_at, t.description, t.channel,
       round((t.min_amt + p.r * (t.max_amt - t.min_amt)) / 1000) * 1000
FROM picks p JOIN templates t ON t.k = p.k;

-- Monthly salary credit and deposit interest so the data looks realistic.
INSERT INTO transactions (account_id, posted_at, description, channel, amount)
SELECT id, date_trunc('month', now()) + interval '24 days' - interval '1 month', 'Gaji bulanan', 'TRANSFER', 12500000
FROM accounts WHERE product = 'TABUNGAN' AND status = 'ACTIVE';
INSERT INTO transactions (account_id, posted_at, description, channel, amount)
SELECT id, date_trunc('month', now()), 'Bunga deposito', 'TELLER', round(balance * 0.045 / 12)
FROM accounts WHERE product = 'DEPOSITO';

-- Make balances consistent: opening balance + sum of all transactions.
UPDATE accounts a SET balance = a.balance + s.total
FROM (SELECT account_id, sum(amount) AS total FROM transactions GROUP BY account_id) s
WHERE s.account_id = a.id;

INSERT INTO card_block_requests (reference, card_id, reason, channel, created_at)
SELECT 'BLK-SEED-000001', id, 'LOST', 'BRANCH', blocked_at FROM cards WHERE card_last4 = '6642';
