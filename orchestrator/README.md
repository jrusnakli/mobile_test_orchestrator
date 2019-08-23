This directory contains the host-side server code to execute test suites via
the am instrument command, interacting with the TestButler system on the phone

General NOTES
-------------

Async command execution provides a mechanism to iterate over lines of stdout asynchronously.  This allows
the code to be cleaner, EXCEPT that the async generator code in current Python 3 can bypass finally-marked
code causing issues.  So what is actually returned is an AsyncContextManager and so the code rather than
looking like this:

async for line in await execute_async_cmd():
     process_line(line)


It instead looks like:

async with await execute_async_cmd() as generate_lines():
    async for line in generated_lines():
        prorcess_line()

Take care to note the async and await placements.