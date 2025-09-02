// C++ manager mirroring the Python reference for communication tasks.
// Args: argv[1]=fifo_from_user, argv[2]=fifo_to_user
// Reads an integer from input.txt, then for i in 10..19 sends (i+input)
// to the user via fifo_to_user, reads a reply line from fifo_from_user and
// expects "correct <x>". If all succeed, sends "0" to request user exit.
// Writes the last received line to output.txt and prints 1 or 0 to stdout.
#include <cstdio>
#include <cstdlib>
#include <signal.h>
#include <string>
#include <cstring>

using namespace std;

static void rstrip(char* s) {
    size_t n = std::strlen(s);
    while (n > 0 && (s[n-1] == '\n' || s[n-1] == '\r')) {
        s[--n] = '\0';
    }
}


int main(int argc, char **argv) {
	signal(SIGPIPE, SIG_IGN);

	FILE *fin, *fout, *fifo_in, *fifo_out;

	fin = fopen("input.txt", "r");
    fout = fopen("output.txt", "w");
	fifo_in = fopen(argv[2], "w");
	fifo_out = fopen(argv[1], "r");

	int input_value;
	fscanf(fin, "%d", &input_value);

    bool correct = true;
    char buf[1024];
    string last_line;

    // for i in list(range(10, 20)) + [0]
    for (int i = 10; i < 20; ++i) {
        int x = i + input_value;
        fprintf(fifo_in, "%d\n", x);
        fflush(fifo_in);

        if (!fgets(buf, sizeof(buf), fifo_out)) { correct = false; break; }
        rstrip(buf);
        last_line = buf;

        char expect[128];
        snprintf(expect, sizeof(expect), "correct %d", x);
        if (strcmp(buf, expect) != 0) { correct = false; break; }
    }

    fprintf(fifo_in, "0\n");
    fflush(fifo_in);

    // Write last received line to output.txt (with newline)
    if (!last_line.empty()) {
        fprintf(fout, "%s\n", last_line.c_str());
        fflush(fout);
    }

	fclose(fin);
    fclose(fout);
	fclose(fifo_in);
	fclose(fifo_out);

    printf(correct ? "1\n" : "0\n");

    return 0;
}
