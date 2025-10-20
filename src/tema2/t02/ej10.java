package tema2.t02;

import java.util.Scanner;

public class ej10 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Introduce el numero de terminos que quieras");
        int N = sc.nextInt();

        int a = 0;
        int b = 1;

        System.out.println("Los primeros " + N + " términos de la serie de Fibonacci son:");

        for (int i = 1; i <= N; i++) {
            System.out.print(a + " ");
            int siguiente = a + b;
            a = b;
            b = siguiente;
        }
    }
}
