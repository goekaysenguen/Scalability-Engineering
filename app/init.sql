CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(50) PRIMARY KEY,
    status VARCHAR(20),
    image_url TEXT,
    result TEXT,
    enqueued_at FLOAT
);