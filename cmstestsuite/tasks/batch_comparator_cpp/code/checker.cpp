
// Comparator for batch task that checks semantic form of user output.
// Args: argv[1]=input.txt (unused), argv[2]=correct_output.txt, argv[3]=user_output.txt
// Accepts if user output equals "correct X" where X is the integer in correct_output.txt.
// Rejects if it equals "incorrect X" or anything else.

#include <cstdio>
#include <cstring>
#include <string>

static void print_success() {
    std::fprintf(stdout, "1.0\n");
    std::fprintf(stderr, "translate:success\n");
}

static void print_failure() {
    std::fprintf(stdout, "0.0\n");
    std::fprintf(stderr, "translate:wrong\n");
}

static void rstrip(char* s) {
    size_t n = std::strlen(s);
    while (n > 0 && (s[n-1] == '\n' || s[n-1] == '\r')) {
        s[--n] = '\0';
    }
}

int main(int argc, char** argv) {
    if (argc < 4) { print_failure(); return 0; }

    // Read expected integer from correct output
    FILE* fexp = std::fopen(argv[2], "rb");
    if (!fexp) { print_failure(); return 0; }
    int x; if (std::fscanf(fexp, "%d", &x) != 1) { std::fclose(fexp); print_failure(); return 0; }
    std::fclose(fexp);

    // Read first line from user output
    FILE* fuser = std::fopen(argv[3], "rb");
    if (!fuser) { print_failure(); return 0; }
    char buf[1024];
    if (!std::fgets(buf, sizeof(buf), fuser)) { std::fclose(fuser); print_failure(); return 0; }
    std::fclose(fuser);
    rstrip(buf);

    char expect_correct[128];
    std::snprintf(expect_correct, sizeof(expect_correct), "correct %d", x);

    if (std::strcmp(buf, expect_correct) == 0) {
        print_success();
    } else {
        print_failure();
    }
    return 0;
}
