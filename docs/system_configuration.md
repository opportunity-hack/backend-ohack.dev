# System Configuration 

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

