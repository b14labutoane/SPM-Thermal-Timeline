DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "spm_thermal",
    "user": "postgres",
    "password": "postgres",
}


def get_connection_string() -> str:
    return (
        f"host={DB_CONFIG['host']} "
        f"port={DB_CONFIG['port']} "
        f"dbname={DB_CONFIG['database']} "
        f"user={DB_CONFIG['user']} "
        f"password={DB_CONFIG['password']}"
    )


def get_sqlalchemy_url() -> str:
    return (
        f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    )
