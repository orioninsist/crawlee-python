-- Atomically reopen an already handled request.
local handled_key = KEYS[1]
local pending_key = KEYS[2]
local queue_key = KEYS[3]
local data_key = KEYS[4]
local metadata_key = KEYS[5]

local unique_key = ARGV[1]
local now = ARGV[2]

if redis.call('SISMEMBER', handled_key, unique_key) ~= 1 then
    return 0
end

local data = redis.call('HGET', data_key, unique_key)
if not data then
    return 0
end

redis.call('SREM', handled_key, unique_key)
redis.call('SADD', pending_key, unique_key)
redis.call('HSET', data_key, unique_key, ARGV[3])
redis.call('RPUSH', queue_key, unique_key)

redis.call('JSON.NUMINCRBY', metadata_key, '$.handled_request_count', -1)
redis.call('JSON.NUMINCRBY', metadata_key, '$.pending_request_count', 1)
redis.call('JSON.SET', metadata_key, '$.modified_at', cjson.encode(now))
redis.call('JSON.SET', metadata_key, '$.accessed_at', cjson.encode(now))

return 1
