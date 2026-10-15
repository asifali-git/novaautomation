<div align="center">

# NOVA

`worker` · `site` · `pipeline` · `flows`

</div>

---

Internal utility. No support. No guarantees.

```
hub      ->  edge runtime + durable store
panel    ->  thin client
batch    ->  on-demand jobs
sched    ->  interval ticks
```

## Notes

- Runtime and store are provisioned independently.
- The panel reads/writes only through the hub.
- Jobs are dispatched, not long-lived.
- Voices and metadata are generated at run time.
- No warranty. Use at your own risk.

## License

Unlicensed. Private use.
