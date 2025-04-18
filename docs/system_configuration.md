# System Configuration 

## Caching with Redis

The application uses a hybrid caching system that works both in development and production:

- In production (on Fly.io): Redis is used for distributed caching
- In development: Local in-memory cache is used as fallback

### Setting up Redis on Fly.io

1. Create a Redis instance on Fly.io:

```bash
fly redis create --name ohack-redis --region sjc
```

2. Attach the Redis instance to the app:

```bash
fly redis attach --app backend-ohack ohack-redis
```

This will automatically set the `REDIS_URL` environment variable for your app.

### Local Development

For local development, no additional configuration is needed. The application will detect the absence of Redis and use local caching automatically.

If you want to test with Redis locally:

1. Install Redis on your machine
2. Start the Redis server: `redis-server`
3. Set the environment variable: `export REDIS_URL=redis://localhost:6379`

## File Descriptor Limits

The application may encounter "Too many open files" errors if the system's file descriptor limits are too low.

### Checking Current Limits

To check the current limits:

```bash
ulimit -n  # Shows the current per-process limit
cat /proc/sys/fs/file-max  # Shows the system-wide limit
```

### Temporary Increase

To temporarily increase the limit for the current session:

```bash
ulimit -n 4096  # Increase to 4096 (adjust as needed)
```

### Permanent Increase

To permanently increase the limits:

1. Edit `/etc/security/limits.conf`:

