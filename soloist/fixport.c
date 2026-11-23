// fixport: LD_PRELOAD shim that pins Soloist's ZeroConf (pairing) port.
//
// Soloist has no option for this port and binds it to 0.0.0.0:0 (random) on
// every start, which would need a new firewall rule each time. This shim
// rewrites the first TCP bind to the wildcard address with port 0 so it uses
// $SOLOIST_ZC_PORT instead. Every other bind (loopback WebSocket, UDP mDNS,
// anything with an explicit port) passes through untouched.
//
// Build: gcc -O2 -shared -fPIC -o soloist-fixport.so fixport.c -ldl
// Use:   LD_PRELOAD=/path/soloist-fixport.so SOLOIST_ZC_PORT=47621 soloist ...
#define _GNU_SOURCE
#include <dlfcn.h>
#include <netinet/in.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

static atomic_int done = 0;

int bind(int fd, const struct sockaddr *addr, socklen_t len) {
    static int (*real_bind)(int, const struct sockaddr *, socklen_t);
    if (!real_bind) real_bind = (int (*)(int, const struct sockaddr *, socklen_t))dlsym(RTLD_NEXT, "bind");

    const char *env = getenv("SOLOIST_ZC_PORT");
    int type = 0;
    socklen_t tl = sizeof type;
    if (env && addr && !atomic_load(&done) &&
        getsockopt(fd, SOL_SOCKET, SO_TYPE, &type, &tl) == 0 && type == SOCK_STREAM) {
        unsigned short port = (unsigned short)atoi(env);
        if (addr->sa_family == AF_INET && len >= (socklen_t)sizeof(struct sockaddr_in)) {
            struct sockaddr_in a;
            memcpy(&a, addr, sizeof a);
            if (a.sin_port == 0 && a.sin_addr.s_addr == htonl(INADDR_ANY)) {
                a.sin_port = htons(port);
                atomic_store(&done, 1);
                return real_bind(fd, (struct sockaddr *)&a, sizeof a);
            }
        } else if (addr->sa_family == AF_INET6 && len >= (socklen_t)sizeof(struct sockaddr_in6)) {
            struct sockaddr_in6 a;
            memcpy(&a, addr, sizeof a);
            if (a.sin6_port == 0 && memcmp(&a.sin6_addr, &in6addr_any, sizeof in6addr_any) == 0) {
                a.sin6_port = htons(port);
                atomic_store(&done, 1);
                return real_bind(fd, (struct sockaddr *)&a, sizeof a);
            }
        }
    }
    return real_bind(fd, addr, len);
}
