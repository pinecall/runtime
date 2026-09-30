"""Blocks of a pool's connection: together in a transaction, marked, together, a lock alone."""

LOCKED = "select 1 from things where id = %s for update"


async def together(pool, one, two):
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(one)
        await connection.execute(two)


async def nested(pool, one, two):
    async with pool.connection() as connection:
        async with connection.transaction():
            await connection.execute(one)
            await connection.execute(two)


async def marked(pool, one, two):
    # independent: two reads, each its own snapshot either way
    async with pool.connection() as connection:
        await connection.execute(one)
        await connection.execute(two)


async def unmarked(pool, one, two):
    async with pool.connection() as connection:
        await connection.execute(one)
        await connection.execute(two)


async def lock_alone(pool):
    async with pool.connection() as connection:
        await connection.execute(LOCKED, (1,))


async def looped(pool, statements):
    async with pool.connection() as connection:
        for statement in statements:
            await connection.execute(statement)
